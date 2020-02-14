import json
import os

from celery.utils.log import get_task_logger
from flask import current_app
from flask import render_template
from influxdb import InfluxDBClient

from ..core.clustermgr_installer import Installer
from ..extensions import celery
from ..extensions import db
from ..extensions import wlogger
from ..models import AppConfiguration
from ..models import Server
from ..core.utils import as_boolean, parse_setup_properties

task_logger = get_task_logger(__name__)


_ELASTIC_YUM_REPO = """[elastic-6.x]
name=Elastic repository for 6.x packages
baseurl=https://artifacts.elastic.co/packages/6.x/yum
gpgcheck=1
gpgkey=https://artifacts.elastic.co/GPG-KEY-elasticsearch
enabled=1
autorefresh=1
type=rpm-md
"""

def _filebeat_to_influx(log, hostname=""):
    """Formats log generated by filebeat to conform to influxdb format.

    :params log: A ``dict`` of log.
    :params hostname: Hostname of the server; if left blank, will use
                      value generated by filebeat.
    :returns: A ``dict`` of formatted log.
    """
    # Example:
    #
    #     {
    #         u'beat': {u'hostname': u'gluu-elk', u'name': u'gluu-elk', u'version': u'5.6.3'},
    #         u'fields': {u'ip': u'172.40.40.40', u'gluu': {u'chroot': True, u'version': u'3.1.1'}, u'os': u'Ubuntu 14.04', u'type': u'httpd'},
    #         u'@timestamp': u'2018-01-19T15:09:12.096Z',
    #         u'source': u'/var/log/apache2/access.log',
    #         u'offset': 972,
    #         u'input_type': u'log',
    #         u'message': u'::1 - - [19/Jan/2018:15:09:11 +0000] "GET /g HTTP/1.1" 404 433 "-" "curl/7.35.0"',
    #     }
    _log = {}
    _log["time"] = log["@timestamp"]
    _log["measurement"] = "logs"
    _log["tags"] = {
        "hostname": hostname or log["beat"]["hostname"],
        "chroot": log["fields"]["gluu"]["chroot"],
        "gluu_version": log["fields"]["gluu"]["version"],
        "ip": log["fields"]["ip"],
        "os": log["fields"]["os"],
        "type": log["fields"]["type"],
    }
    _log["fields"] = {k: v for k, v in log.iteritems() if k in ("message", "source")}
    return _log


def parse_log(log, influx_fmt=True, hostname=""):
    """Parses log.

    :params log: A plain-text log message.
    :params influx_fmt: Whether to use influxdb format or not.
    :params hostname: Hostname of the server where log is collected from.
    :returns: A ``dict`` of formatted log or ``None``
    """
    json_log = None

    try:
        json_log = json.loads(log)
        if influx_fmt:
            json_log = _filebeat_to_influx(json_log, hostname)
    except ValueError as exc:
        # something is wrong when converting string into dict
        task_logger.warn("unable to parse the log; reason={}".format(exc))
    return json_log


@celery.task(bind=True)
def collect_logs(self, server_id, path, influx_fmt=True):
    """Collects logs from a server.

    :params server_id: ID of a server.
    :params path: Absolute path to file contains logs.
    :params influx_fmt: Whether to use influxdb format or not.
    :returns: A boolean whether logs are saved successfully to database or not.
    """
    task_id = self.request.id
    app_conf = AppConfiguration.query.first()
    dbname = current_app.config["INFLUXDB_LOGGING_DB"]
    logs = []
    server = Server.query.get(server_id)
    
    agent_time = ''
    agent_type = ''
    
    influx = InfluxDBClient(database=dbname)
    influx.create_database(dbname)
    influx_query = "SELECT * FROM logs WHERE hostname='{}' order by time desc limit 1".format(server.hostname)
    result = influx.query(influx_query)

    if result: 
        rdict = next(result.get_points())
        agent_time = rdict['time']
        agent_type = rdict['type']

    cmd = '/usr/local/bin/getfilebeatlog.py time:{} type:{}'.format(agent_time, agent_type)

    installer = Installer(
        server,
        app_conf.gluu_version,
        logger_task_id=task_id,
        server_os=server.os,
        server_id=server_id,
    )

    try:
        _, stdout, stderr = installer.run(cmd, inside=False)
        if not stderr:
            logs = filter(
                None,
                [parse_log(log, hostname=server.hostname) for log in stdout.splitlines()],
            )
        else:
            task_logger.warn("Unable to collect logs from remote server {}/{}; "
                             "reason={}".format(server.hostname, server.ip, stderr))
    except Exception as exc:
        task_logger.warn("Unable to collect logs from remote server; "
                         "reason={}".format(exc))


    return influx.write_points(logs)


def _install_filebeat(installer):
    """Installs filebeat.

    Docs at https://www.elastic.co/guide/en/beats/filebeat/current/setup-repositories.html.
    """

    if installer.clone_type == 'deb':
        cmd_list = [
            "wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch | APT_KEY_DONT_WARN_ON_DANGEROUS_USAGE=DontWarn  apt-key add -",
            "echo 'deb https://artifacts.elastic.co/packages/6.x/apt stable main' | tee /etc/apt/sources.list.d/elastic-6.x.list",
        ]
    else:
        cmd_list = [
            "rpm --import https://packages.elastic.co/GPG-KEY-elasticsearch",
            "echo '{}' > /etc/yum.repos.d/elastic.repo".format(_ELASTIC_YUM_REPO),
        ]

    for cmd in cmd_list:
        installer.run(cmd, inside=False)


    if installer.clone_type == 'deb':
        installer.install('apt-transport-https', inside=False)
        installer.install('filebeat', inside=False)
        installer.run('update-rc.d filebeat defaults 95 10', inside=False)
    else:
        installer.install('filebeat', inside=False)
        installer.run('chkconfig --add filebeat', inside=False)


def _render_filebeat_config(installer):
    """Renders filebeat config and upload to a server.
    """
    # render filebeat.yml and upload to server

    with current_app.app_context():
        ctx = {
            "ip": installer.ip,
            "os": installer.server_os,
            "chroot": "true" if installer.is_gluu_installed() else "",
            "chroot_path": installer.container,
            "gluu_version": installer.gluu_version,
            "input_passport": "",
            "input_shibboleth": "",
        }


        if installer.server_os in ('CentOS 7', 'RHEL 7'):
            ctx['apache_paths'] = ('    - %(chroot_path)s/var/log/httpd/access_log\n'
                                   '    - %(chroot_path)s/var/log/httpd/error_log\n'
                                   ) % ctx

        else:
            ctx['apache_paths'] = ('    - %(chroot_path)s/var/log/apache2/access.log\n'
                                   '    - %(chroot_path)s/var/log/apache2/error.log\n'
                                   '    - %(chroot_path)s/var/log/apache2/other_vhosts_access.log'
                                   ) % ctx

        prop = parse_setup_properties(
                os.path.join(current_app.config['DATA_DIR'], 'setup.properties')
            )
        
        input_tmp = (
                '- input_type: log\n'
                '  paths:\n'
                '%(log_files)s'
                '  multiline.pattern: \'^[0-9]{4}-[0-9]{2}-[0-9]{2}\'\n'
                '  multiline.negate: true\n'
                '  multiline.match: after\n'
                '  fields:\n'
                '    gluu:\n'
                '      version: %(gluu_version)s\n'
                '      chroot: %(chroot)s\n'
                '    ip: %(ip)s\n'
                '    os: %(os)s\n'
                '    type: %(type)s\n'
                )
                
        
        if as_boolean(prop['installPassport']):
            ctx_ = ctx.copy()
            ctx_.update({
                        'type': 'passport', 
                        'log_files': '    - /opt/{}/opt/gluu/node/passport/server/logs/passport.log\n'.format(installer.container)
                        })
            ctx['input_passport'] = input_tmp % ctx_

    
        if as_boolean(prop['installSaml']):
            ctx_ = ctx.copy()
            ctx_.update({
                        'type': 'shibboleth', 
                        'log_files': ('    - /opt/{0}/opt/shibboleth-idp/logs/idp-process.log\n'
                                      '    - /opt/{0}/opt/shibboleth-idp/logs/idp-warn.log\n'
                                      '    - /opt/{0}/opt/shibboleth-idp/logs/idp-audit.log\n'
                                     ).format(installer.container)
                        })
            ctx['input_shibboleth'] = input_tmp % ctx_

        yml = render_template("filebeat/filebeat.yml", **ctx)
        installer.put_file("/etc/filebeat/filebeat.yml", yml)


@celery.task(bind=True)
def setup_filebeat(self, force_install=False):
    """Setup filebeat to collect logs.
    """
    task_id = self.request.id
    servers = Server.query.all()
    app_conf = AppConfiguration.query.first()

    print "TASK", task_id

    local_agent_file = os.path.join(current_app.root_path, 'monitoring_scripts/getfilebeatlog.py')
    remote_agent_file = '/usr/local/bin/getfilebeatlog.py'

    for server in servers:

        installer = Installer(
            server,
            app_conf.gluu_version,
            logger_task_id=task_id,
            server_os=server.os
            )

        installer.upload_file(local_agent_file, remote_agent_file)
        installer.run('chmod +x ' + remote_agent_file, False)

        fb_installed = installer.conn.exists('/usr/bin/filebeat')

        if app_conf.offline:
            if not fb_installed:
                wlogger.log(
                        task_id,
                        "Filebeat was not installed on this server. Please"
                        " install and retry", "error", server_id=server.id)
                return False

        else:
            if (not fb_installed) or force_install:
                # installs filebeat
                _install_filebeat(installer)

        # renders filebeat config
        _render_filebeat_config(installer)

        # restarts filebeat service
        # note, restarting filebeat service may gives unwanted output,
        # hence we skip checking the result of running command
        installer.restart_service('filebeat', inside=False)

        # update the model
        server.filebeat = True
        db.session.commit()


# @celery.task(bind=True)
def setup_influxdb(self):
    """Setup influxdb database.
    """
    tid = self.request.id

    # @TODO: do we need to install influxdb locally?
    dbname = current_app.config["INFLUXDB_LOGGING_DB"]

    wlogger.log(
        tid,
        "Creating InfluxDB database {}".format(dbname),
        "info",
    )

    influx = InfluxDBClient(database=dbname)
    influx.create_database(dbname)
    return True


@celery.task(bind=True)
def remove_filebeat(self):
    """Removes filebeat.
    """
    task_id = self.request.id
    app_conf = AppConfiguration.query.first()

    servers = Server.query.all()

    for server in servers:
        installer = Installer(
            server,
            app_conf.gluu_version,
            logger_task_id=task_id,
            server_os=server.os
            )

        installer.remove('filebeat', inside=False)

        # remove log used to collect all components logs
        installer.run("rm -f /tmp/gluu-filebeat*", inside=False)

        # update the model
        server.filebeat = False
        db.session.add(server)
        db.session.commit()

    return True
