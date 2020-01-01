# Instal CM on RedHat 7


If you don't have registered RHEL7 repo, write the following content to `/etc/yum.repos.d/centos7.repo`

```
[centos]
name=CentOS-7
baseurl=http://ftp.heanet.ie/pub/centos/7/os/x86_64/
enabled=1
gpgcheck=1
gpgkey=http://ftp.heanet.ie/pub/centos/7/os/x86_64/RPM-GPG-KEY-CentOS-7

```

`# rpm -i https://dl.fedoraproject.org/pub/epel/epel-release-latest-7.noarch.rpm`

`# yum repolist`

`# yum install gcc gcc-c++ make python-devel  openldap-devel python-pip`

`# yum install -y java-1.8.0-openjdk-headless redis`

`# pip install python-ldap`

`# pip install clustermgr4` [ If from github then: `pip install https://github.com/GluuFederation/cluster-mgr/archive/4.0.zip` ] 

`# systemctl enable redis`

`# systemctl start redis`


# Install CM on CentOS 7

`# yum install -y epel-release`

`# yum repolist`

`# yum install java-1.7.0-openjdk-headles`

`# yum install gcc gcc-c++ make python-devel  openldap-devel python-pip java-1.8.0-openjdk-headless`

`# yum install -y redis`

`# pip install python-ldap`

`# pip install clustermgr4`

`# systemctl enable redis`

`# systemctl start redis`