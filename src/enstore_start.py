#!/usr/bin/env python

###############################################################################
# src/$RCSfile$   $Revision$
#
# start enstore
#
#
#


# system imports
#
import sys
import re
import os
import string
import errno
import socket
import pwd

# enstore imports
import setpath
import e_errors
import enstore_functions
import enstore_functions2
import udp_client
import generic_client
import option

import alarm_client
import configuration_client
import configuration_server
import file_clerk_client
import inquisitor_client
import library_manager_client
import log_client
import media_changer_client
import mover_client
import monitor_client
import volume_clerk_client
import ratekeeper_client
import event_relay_client


def get_csc():
    # get a configuration server
    config_host = enstore_functions2.default_host()
    config_port = enstore_functions2.default_port()
    csc = configuration_client.ConfigurationClient((config_host,config_port))

    return csc

def this_host():
    rtn = socket.gethostbyaddr(socket.getfqdn())

    return [rtn[0]] + rtn[1] + rtn[2]

def is_on_host(host):

    if host in this_host():
        return 1

    return 0

#Return true if the system is in one of the production systems.
def is_in_cluster():

    #If we are on the configuration server host, check the config file
    # directly.
    if os.environ['ENSTORE_CONFIG_HOST'] in this_host():
        conf_dict = enstore_functions.get_config_dict().configdict
        kcs = conf_dict.get('known_config_servers')
        
    #Any other system we need to check with the configuration server.
    else:    
        csc = get_csc()
        kcs = csc.get('known_config_servers', 3, 3)

    #Simple loop to determine if the system is a production system.
    for cluster in kcs.keys():
        if cluster == socket.gethostname()[:len(cluster)]:
            return 1

    return 0

#If the system is in a production cluster make in run as user enstore
# if possible.
def check_user():

    #If in a cluster system...
    if is_in_cluster():
        #First determine if running as root, if so become enstore and restart.
        if os.getegid() == 0:
            os.execvp("su",
                  string.split("su enstore -c \"%s\"" % string.join(sys.argv)))
        #Extract the user name.
	try:
            name = pwd.getpwuid(os.geteuid())[0]
	except (KeyError, IndexError):
	    name = ""
        #Check if running as user enstore.
        if name != "enstore":
            print "You should run this as user enstore."
            sys.exit(1)
            
def check_csc(csc):

    info = csc.get("configuration_server", 5, 3)
    if not info.get('host', None) in this_host() and \
           not info.get('hostip', None) in this_host():
        return

    rtn = csc.alive(configuration_client.MY_SERVER, 5, 3)

    if not e_errors.is_ok(rtn):
        if csc.server_address[0] in this_host():
            #Start the configuration server.
            print "Starting configuration server: %s:%s" % csc.server_address
            os.system("python $ENSTORE_DIR/src/configuration_server.py "\
                      "--config-file $ENSTORE_CONFIG_FILE &")

            #Check the restart csc.
            rtn = csc.alive(configuration_client.MY_SERVER, 5, 3)
            if not e_errors.is_ok(rtn):
                print "Configuration server not started."
                sys.exit(1)
        else:
            print "Configuration server not running."
            sys.exit(1)
    else:
        print "Configuration server found: %s:%s" % csc.server_address

def check_db(csc, name, cmd):

    info = csc.get("volume_clerk", 5, 3)
    if not info.get('host', None) in this_host() and \
           not info.get('hostip', None) in this_host():
        return

    print "Checking %s." % name

    rtn = os.popen("ps -elf | grep %s | grep -v grep" % (name,)).readlines()

    if not rtn:
        print "Starting %s." % name
        os.system(cmd)

#If the event relay responded to alive messages, this would not be necessary.
def check_event_relay(csc, cmd):

    info = csc.get("event_relay", 5, 3)
    if not info.get('host', None) in this_host() and \
           not info.get('hostip', None) in this_host():
        return

    erc = event_relay_client.EventRelayClient(event_relay_host=info['hostip'],
                                              event_relay_port=info['port'])

    print "Checking event_relay."

    rtn = erc.alive()

    if rtn:
        print "Starting event_relay."
        os.system(cmd)
    else:
        print "Found event_relay."

def check_server(csc, name, cmd):
    
    #Initialize, send and receive alive responce.
    u = udp_client.UDPClient()
    info = csc.get(name, 5, 3)
    if not info.get('host', None) in this_host() and \
           not info.get('hostip', None) in this_host():
        return

    print "Checking %s." % name

    #Send and receive responce.
    try:
        rtn = u.send({'work':"alive"}, (info['hostip'], info['port']), 3, 3)
    except errno.errorcode[errno.ETIMEDOUT]:
        rtn = {'status':(e_errors.TIMEDOUT, errno.errorcode[errno.ETIMEDOUT])}
        
    #Process responce.
    if not e_errors.is_ok(rtn):
        #Start the server.
        print "Starting %s: %s:%s" % (name, info['hostip'], info['port'])
        os.system(cmd)
        
        
        #Check the restart csc.
        rtn = csc.alive(name, 3, 3)
        if not e_errors.is_ok(rtn):
            print "Server %s not started." % (name,)
            sys.exit(1)
    else:
        print "Found %s: %s:%s" % (name, info.get('hostip', None),
                                   info.get('port', None))
    

class EnstoreStartInterface(generic_client.GenericClientInterface):

    def __init__(self, args=sys.argv, user_mode=1):
        self.name = "START"
        self.just = None

        generic_client.GenericClientInterface.__init__(self, args=args,
                                                       user_mode=user_mode)

    def valid_dictionaries(self):
        return (self.help_options, self.start_options)
    
    # parse the options like normal but make sure we have other args
    def parse_options(self):

        generic_client.GenericClientInterface.parse_options(self)

        self.completed_server_name()

    def completed_server_name(self):
        if self.just:
            count = 0
            for name in self.complete_names:
                if self.just == name[:len(self.just)]:
                    count = count + 1
                    hold = name
            if count == 1:
                self.just = hold

    def should_start(self, server_name):

        if self.just == server_name or self.just == None:
            return 1

        return 0

    complete_names = [
        "configuration_server",
        "event_relay",
        "log_server",
        "alarm_server",
        "volume_clerk",
        "file_clerk",
        "db_checkpoint",
        "db_deadlock",
        "inquisitor",
        "ratekeeper",
        "library",
        "media",
        "mover",
        ]
        

    start_options = {
        option.JUST:{option.HELP_STRING:"specify single server",
                     option.VALUE_NAME:"just",
                     option.VALUE_USAGE:option.REQUIRED,
                     option.VALUE_TYPE:option.STRING,
		     option.USER_LEVEL:option.ADMIN,
                     }
        }

def do_work(intf):

    check_user()

    csc = get_csc()
    
    if intf.should_start("configuration_server"):
        check_csc(csc)

    rtn = csc.alive(configuration_client.MY_SERVER, 5, 3)
    if not e_errors.is_ok(rtn):
        #If the configuration server was not specifically specified.
        print "Configuration server not running."
        sys.exit(1)
            
    # We know the config server is up.  Get the database info.
    db_dir = csc.get('database', {}).get('db_dir', None)
    if not db_dir:
        print "Unable to determine database directory."
        sys.exit(1)

    #The movers need to run as root, check for sudo.
    if os.system("sudo -V > /dev/null 2> /dev/null"): #if true sudo not found.
        sudo = ""
    else:
        sudo = "sudo"  #found

    #Start the event relay.

    if intf.should_start("event_relay"):
        check_event_relay(csc, "python $ENSTORE_DIR/src/event_relay.py &")

    #Start the Berkley DB dameons.
    if intf.should_start("db_checkpoint"):
        check_db(csc, "db_checkpoint",
                 "db_checkpoint -h %s  -p 5 &" % db_dir)
    if intf.should_start("db_deadlock"):
        check_db(csc, "db_deadlock",
                 "db_deadlock -h %s  -t 1 &" % db_dir)
        
    for server in ["log_server", "alarm_server", "volume_clerk", "file_clerk",
                   "inquisitor", "ratekeeper"]:
        if intf.should_start(server):
            check_server(csc, server,
                         "python $ENSTORE_DIR/src/%s.py &" % server)

    #Get the library names.
    libraries = csc.get_library_managers({}).keys()
    libraries = map((lambda l: l + ".library_manager"), libraries)

    #Libraries.
    if intf.should_start("library"):
        for library in libraries:
            check_server(csc, library,
                   "python $ENSTORE_DIR/src/library_manager.py %s &" % library)
    elif intf.just[-16:] == ".library_manager":
        check_server(csc, intf.just,
                 "python $ENSTORE_DIR/src/library_manager.py %s &" % intf.just)

    #Media changers.
    if intf.should_start("media"):
        for library in libraries:
            media = csc.get_media_changer(library)
            check_server(csc, media,
                     "python $ENSTORE_DIR/src/media_changer.py %s &" % media)
    elif intf.just[-14:] == ".media_changer":
        check_server(csc, intf.just,
                 "python $ENSTORE_DIR/src/media_changer.py %s &" % intf.just)

    #Movers.
    if intf.should_start("mover"):
        for library in libraries:
            for mover in csc.get_movers(library):
                mover = mover['mover']
                check_server(csc, mover,
                    "%s python $ENSTORE_DIR/src/mover.py %s &" % (sudo, mover))
    elif intf.just[-6:] == ".mover":
        check_server(csc, intf.just,
                "%s python $ENSTORE_DIR/src/mover.py %s &" % (sudo, intf.just))

if __name__ == '__main__':

    intf = EnstoreStartInterface(user_mode=0)

    do_work(intf)
