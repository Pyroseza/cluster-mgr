"""A Flask blueprint with the views and logic dealing with the Cache Management
of Gluu Servers"""
import os

from flask import Blueprint, render_template, url_for, flash, redirect, \
    jsonify, request, session

from flask_login import login_required

from clustermgr.models import Server, AppConfiguration
from clustermgr.tasks.cache import install_cache_cluster


from ..core.license import license_reminder
from ..core.license import prompt_license
from ..core.license import license_required
from clustermgr.core.remote import RemoteClient
from clustermgr.core.utils import get_redis_config, get_cache_servers, random_chars
from clustermgr.forms import CacheSettingsForm, cacheServerForm

from clustermgr.models import db, CacheServer


cache_mgr = Blueprint('cache_mgr', __name__, template_folder='templates')
cache_mgr.before_request(prompt_license)
cache_mgr.before_request(license_required)
cache_mgr.before_request(license_reminder)


@cache_mgr.route('/')
@login_required
def index():
    servers = Server.query.all()
    appconf = AppConfiguration.query.first()

    if not appconf:
        flash("The application needs to be configured first. Kindly set the "
              "values before attempting clustering.", "warning")
        return redirect(url_for("index.app_configuration"))

    if not servers:
        flash("Add servers to the cluster before attempting to manage cache",
              "warning")
        return redirect(url_for('index.home'))


    version = int(appconf.gluu_version.replace(".", ""))
    if version < 311:
        flash("Cache Management is available only for clusters configured with"
              " Gluu Server version 3.1.1 and above", "danger")
        return redirect(url_for('index.home'))

    form = CacheSettingsForm()

    cache_servers = get_cache_servers()

    print cache_servers

    return render_template('cache_index.html', 
                           servers=servers, 
                           form=form,
                           cache_servers=cache_servers,
                           )


def get_servers_and_list():
    server_id = request.args.get('id')
    
    if server_id:
        servers = [ Server.query.get(int(server_id)) ]
    else:
        servers = Server.query.all()

    server_id_list = [ s.id for s in servers ]
    
    return servers, server_id_list, server_id

@cache_mgr.route('/install', methods=['GET', 'POST'])
@login_required
def install():

    server_id = request.args.get('server')
    
    if server_id:
        cache_servers = []
        servers = [ Server.query.get(int(server_id)) ]

    else:
        cache_servers = get_cache_servers()
        servers = Server.query.all()

    if not servers:
        return redirect(url_for('cache_mgr.index'))


    task = install_cache_cluster.delay(
                                [server.id for server in servers],
                                [server.id for server in cache_servers],
                                )

    return render_template( 'cache_install_logger.html',
                            servers=cache_servers+servers,
                            step=1,
                            task_id=task.id,
                            server_id=server_id,
                           )

@cache_mgr.route('/addcacheserver/', methods=['GET', 'POST'])
@login_required
def add_cache_server():
    cid = request.args.get('cid', type=int)

    form = cacheServerForm()

    if cid:
        cacheserver = CacheServer.query.get(cid)
        form = cacheServerForm(obj=cacheserver)
        if not cacheserver:
            return "<h2>No such Cache Server</h2>"
    else:
        form.redis_password.data = random_chars(20)
        form.stunnel_port.data = 16379

    if request.method == "POST" and form.validate_on_submit():
        hostname = form.hostname.data
        ip = form.ip.data
        install_redis = form.install_redis.data
        redis_password = form.redis_password.data
        stunnel_port = form.stunnel_port.data

        if not cid:
            cacheserver = CacheServer()
            db.session.add(cacheserver)

        cacheserver.hostname = hostname
        cacheserver.ip = ip
        cacheserver.install_redis = install_redis
        cacheserver.redis_password = redis_password
        cacheserver.stunnel_port = stunnel_port

        db.session.commit()
        if cid:
            flash("Cache server was added","success")
        else:
            flash("Cache server was updated","success")

        return jsonify( {"result": True, "message": "Cache server was added"})
    
    return render_template( 'cache_server.html', form=form)

@cache_mgr.route('/status/')
@login_required
def get_status():

    status={'redis':{}, 'stunnel':{}}
    servers = Server.query.all()
    
    check_cmd = 'python -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);print s.connect_ex((\'{0}\', {1}))"'
    
    cache_servers = get_cache_servers()
    

    stunnel_port = cache_servers[0].stunnel_port if cache_servers else None
        
    
    for server in servers + cache_servers:
        key = server.ip.replace('.','_')

        c = RemoteClient(host=server.hostname, ip=server.ip)
        try:
            c.startup()
        except:
            status['stunnel'][key] = False
            status['redis'][key] = False
        else:

            status['stunnel'][key]=False
            
            if server in cache_servers:
                r = c.run(check_cmd.format('localhost', 6379))
                stat = r[1].strip()
                
                if stat == '0':
                    status['redis'][key]=True
                else:
                    status['redis'][key]=False

                if stunnel_port:
                    r = c.run(check_cmd.format(server.ip, stunnel_port))
                    stat = r[1].strip()

                if stat == '0':
                    status['stunnel'][key]=True

            else:
                
                if stunnel_port:

                    r = c.run(check_cmd.format('localhost', '6379'))
                    stat = r[1].strip()

                    if stat == '0':
                        status['stunnel'][key]=True

        c.close()
    
    return jsonify(status)

