#!/usr/bin/env python

import os
import sys
import string
import tempfile
import time
import errno
import select
import e_errors
import timeofday

import configuration_client
import log_client
import alarm_client
import inquisitor_client
import file_clerk_client
import volume_clerk_client
import library_manager_client
import media_changer_client
import mover_client
import generic_client
import enstore_constants
import enstore_functions
import enstore_files
import event_relay_client
import event_relay_messages

DEFAULT = "default"
# default number of times in a row a server can be down before mail is sent
DEFAULTALLOWEDDOWN = [2, 15]
MOVERALLOWEDDOWN = [7, 120]
mail_sent = 0
prefix = ""
do_output = 0
no_mail = 0
SYSTEM = 'system'
ALLOWED_DOWN = 'allowed_down'
TRIES = 1
NOUPDOWN = "noupdown"
TRUE = 1
FALSE = 0
WAIT_THIS_AMOUNT = 120

LOW_CAPACITY = 0
SUFFICIENT_CAPACITY = 1

def sortit(adict):
    keys = adict.keys()
    keys.sort()
    return keys

def enprint(text):
    if do_output:
	print prefix, timeofday.tod(), text

def too_long(start):
    now = time.time()
    if now - start > WAIT_THIS_AMOUNT:
	# we have waited long enough
	rtn = 1
    else:
	rtn = 0
    return rtn

def get_allowed_down_index(server, allowed_down, index):
    if allowed_down.has_key(server):
	rtn = allowed_down[server][index]
    elif enstore_functions.is_mover(server):
	rtn = allowed_down.get(enstore_constants.MOVER,
                               MOVERALLOWEDDOWN)[index]
    elif enstore_functions.is_library_manager(server):
	rtn = allowed_down.get(enstore_constants.LIBRARY_MANAGER,
                               DEFAULTALLOWEDDOWN)[index]
    elif enstore_functions.is_media_changer(server):
	rtn = allowed_down.get(enstore_constants.MEDIA_CHANGER,
                               DEFAULTALLOWEDDOWN)[index]
    else:
	rtn = allowed_down.get(DEFAULT, DEFAULTALLOWEDDOWN)[index]
    return rtn

def is_allowed_down(server, allowed_down):
    return get_allowed_down_index(server, allowed_down, 0)

def get_timeout(server, allowed_down):
    return get_allowed_down_index(server, allowed_down, 1)

def enstore_state(status):
    # given the status accumulated from all of the servers, determine the state of enstore
    if status == enstore_constants.UP:
	rtn = status
    elif status & enstore_constants.DOWN:
	rtn = enstore_constants.DOWN
    elif status & enstore_constants.WARNING:
	rtn = enstore_constants.WARNING
    else:
	rtn = enstore_constants.SEEN_DOWN
    return rtn

def get_allowed_down_dict():
    cdict = enstore_functions.get_config_dict()
    return cdict.configdict.get(SYSTEM, {}).get(ALLOWED_DOWN, {})

class EnstoreServer:

    def __init__(self, name, format_name, offline_d, seen_down_d, allowed_down_d,
		 en_status, cs=None, mailer=None):
	self.name = name
	self.format_name = format_name
	self.offline_d = offline_d
	self.seen_down_d = seen_down_d
	self.allowed_down = is_allowed_down(self.name, allowed_down_d)
	self.timeout = get_timeout(self.name, allowed_down_d)
	self.tries = TRIES
	self.status = enstore_constants.UP
        self.mail_file = None
	self.in_bad_state = 0
	# if self.status is not UP, then enstore is the following
	self.en_status = en_status
	if cs:
	    self.csc = cs.csc
	    self.config_host = cs.config_host
	    # we need to see if this server should be monitored by up_down.  this 
	    # info is in the config file.
	    config_d = self.csc.get(name, self.timeout, self.tries);
	    if config_d.has_key(NOUPDOWN):
		self.noupdown = TRUE
	    else:
		self.noupdown = FALSE
	else:
	    self.csc = None
	    self.noupdown = FALSE

    def is_really_down(self):
        rc = 0
        if self.seen_down_d.get(self.format_name, 0) > self.allowed_down:
            rc = 1
        return rc

    def need_to_send_mail(self):
        rc = 0
        if (self.seen_down_d.get(self.format_name, 0) % self.allowed_down) == 0:
            rc = 1
        return rc

    def writemail(self, message):
        # we only send mail if the server has been seen down more times than it is allowed
        # to be down in a row.
        if self.seen_down_d.has_key(self.format_name) and self.need_to_send_mail():
            # see if this server is known to be down, if so, then do not send mail
            if not self.offline_d.has_key(self.format_name):
                # first get a tempfile
                self.mail_file = tempfile.mktemp()
                os.system("date >> %s"%(self.mail_file,))
                os.system('echo "\t%s" >> %s' % (message, self.mail_file))

    def remove_mail(self):
        if self.mail_file:
            os.system("rm %s"%(self.mail_file,))
            
    def set_status(self, status):
	self.status = status
	if status == enstore_constants.DOWN:
	    self.seen_down_d[self.format_name] = self.seen_down_d.get(self.format_name, 0) + 1
	    if not self.in_bad_state and not self.is_really_down():
		self.status = enstore_constants.SEEN_DOWN
	elif status == enstore_constants.WARNING:
	    self.seen_down_d[self.format_name] = self.seen_down_d.get(self.format_name, 0) + 1
	elif status == enstore_constants.UP:
	    if self.seen_down_d.has_key(self.format_name):
		del self.seen_down_d[self.format_name]

    def is_alive(self):
	enprint("%s ok"%(self.format_name,))
	self.set_status(enstore_constants.UP)

    def is_dead(self):
	enprint("%s NOT RESPONDING"%(self.format_name,))
	self.writemail("%s is not alive. Down counter %s"%(self.format_name, 
							   self.seen_down_d.get(self.format_name, 0)))
	self.set_status(enstore_constants.DOWN)

    def known_down(self):
	self.status = enstore_constants.DOWN
	enprint("%s known down"%(self.format_name,))

    def get_enstore_state(self, state):
	if self.status == enstore_constants.DOWN:
	    # en_status records the state of enstore when the server is done
	    return state | self.en_status
	elif self.status == enstore_constants.WARNING:
	    return state | enstore_constants.WARNING
	elif self.status == enstore_constants.SEEN_DOWN:
	    return state | enstore_constants.SEEN_DOWN
	else:
	    return state

    # the third parameter is used to determine the state of enstore if this server is 
    # considered down.  some servers being down will mark enstore as down, others will
    # not. 'rtn' records the state of the server.
    def check(self, ticket):
	if not 'status' in ticket.keys():
	    # error during alive
	    self.is_dead()
	elif ticket['status'][0] == e_errors.OK:
	    self.is_alive()
	else:
	    if ticket['status'][0] == e_errors.TIMEDOUT:
		self.is_dead()
	    else:
		enprint("%s  BAD STATUS %s"%(self.format_name, ticket['status']))
		self.set_status(enstore_constants.DOWN)
		self.writemail("%s  BAD STATUS %s. Down counter %s"%(self.format_name,
								     ticket['status'],
							   self.seen_down_d.get(self.format_name, 0)))

    def handle_general_exception(self):
	exc, msg, tb = sys.exc_info()
	EnstoreServer.check(self, {'status': (str(exc), str(msg))})
	raise exc, msg


class LogServer(EnstoreServer):

    def __init__(self, csc, offline_d, seen_down_d, allowed_down_d):
	EnstoreServer.__init__(self, "log_server", enstore_constants.LOGS,
			       offline_d, seen_down_d, allowed_down_d,
			       enstore_constants.DOWN, csc)

class AlarmServer(EnstoreServer):

    def __init__(self, csc, offline_d, seen_down_d, allowed_down_d):
	EnstoreServer.__init__(self, "alarm_server", enstore_constants.ALARMS,
			       offline_d, seen_down_d, allowed_down_d,
			       enstore_constants.DOWN, csc)

class ConfigServer(EnstoreServer):

    def __init__(self, offline_d, seen_down_d, allowed_down_d):
	EnstoreServer.__init__(self, "config_server", 
			       enstore_constants.CONFIGS, offline_d,
			       seen_down_d, allowed_down_d,
			       enstore_constants.DOWN)
	self.config_port = string.atoi(os.environ.get('ENSTORE_CONFIG_PORT', 7500))
	self.config_host = os.environ.get('ENSTORE_CONFIG_HOST', "localhost")
	self.csc = configuration_client.ConfigurationClient((self.config_host, 
							     self.config_port))
	enprint("Checking Enstore on %s with variable timeout and tries "%((self.config_host,
									    self.config_port),))

class FileClerk(EnstoreServer):

    def __init__(self, csc, offline_d, seen_down_d, allowed_down_d):
	EnstoreServer.__init__(self, "file_clerk", enstore_constants.FILEC,
			       offline_d, seen_down_d, allowed_down_d,
			       enstore_constants.DOWN, csc)

class Inquisitor(EnstoreServer):

    def __init__(self, csc, offline_d, seen_down_d, allowed_down_d):
	EnstoreServer.__init__(self, "inquisitor", enstore_constants.INQ,
			       offline_d, seen_down_d, allowed_down_d,
			       enstore_constants.WARNING, csc)

class VolumeClerk(EnstoreServer):

    def __init__(self, csc, offline_d, seen_down_d, allowed_down_d):
	EnstoreServer.__init__(self, "volume_clerk", enstore_constants.VOLC,
			       offline_d, seen_down_d, allowed_down_d,
			       enstore_constants.DOWN, csc)

class LibraryManager(EnstoreServer):

    # states of a library manager meaning 'alive but not available for work'
    BADSTATUS = ['ignore', 'locked', 'pause', 'unknown']

    def __init__(self, csc, name, offline_d, seen_down_d, allowed_down_d):
	EnstoreServer.__init__(self, name, name, offline_d, seen_down_d, allowed_down_d,
			       enstore_constants.DOWN, csc)
	self.postfix = enstore_constants.LIBRARY_MANAGER
	self.server_state = ""
	self.in_bad_state = 0

    # return the number of movers we know about that have a good status, and those with a bad
    # status
    def mover_status(self):
	ok_movers = 0
	bad_movers = 0
	for mover in self.movers:
	    if mover.status == enstore_constants.UP:
		ok_movers = ok_movers + 1
	    else:
		bad_movers = bad_movers + 1
	if bad_movers > ok_movers:
	    return LOW_CAPACITY, bad_movers, ok_movers
	else:
	    return SUFFICIENT_CAPACITY, bad_movers, ok_movers

    def is_alive(self):
	# now that we know this lm is alive we need to examine its state
	if self.server_state in self.BADSTATUS:
	    self.in_bad_state = 1
	    # the lm is not in a good state mark it as yellow
	    enprint("%s in a %s state"%(self.format_name, self.server_state))
	    self.set_status(enstore_constants.WARNING)
            if self.server_state == 'unknown':
                self.writemail("%s is in %s state. Down counter %s"%(self.format_name,
                                                                     self.server_state,
                                                           self.seen_down_d.get(self.format_name, 0)))
	else:
	    self.in_bad_state = 0
	    EnstoreServer.is_alive(self)

    def get_enstore_state(self, state):
	# THIS IS A BLOODY HACK THAT SHOULD BE REMOVED ASAP
	if self.name == "samm2.library_manager":
	    if self.mover_status()[0] == LOW_CAPACITY:
		return state | enstore_constants.WARNING
	    else:
		return EnstoreServer.get_enstore_state(self, state)
	# END OF BLOODY HACK

	if self.mover_status()[0] == LOW_CAPACITY:
	    return state | enstore_constants.DOWN
	else:
	    return EnstoreServer.get_enstore_state(self, state)

class MediaChanger(EnstoreServer):

    def __init__(self, csc, name, offline_d, seen_down_d, allowed_down_d):
	EnstoreServer.__init__(self, name, name, offline_d, seen_down_d, allowed_down_d,
			       enstore_constants.DOWN, csc)
	self.postfix = enstore_constants.MEDIA_CHANGER

class Mover(EnstoreServer):

    # states of a mover meaning 'alive but not available for work'
    BADSTATUS = {'ERROR' : enstore_constants.DOWN, 
		 'OFFLINE' : enstore_constants.WARNING,
		 'DRAINING' : enstore_constants.WARNING}

    def __init__(self, csc, name, offline_d, seen_down_d, allowed_down_d):
	EnstoreServer.__init__(self, name, name, offline_d, seen_down_d, allowed_down_d,
			       enstore_constants.WARNING, csc)
	self.postfix = enstore_constants.MOVER
	self.server_state = ""
        self.check_result = 0
	self.in_bad_state = 0

    def is_alive(self):
	# check to see if the mover is in a bad state
	keys = self.BADSTATUS.keys()
	if self.server_state in keys:
	    self.in_bad_state = 1
	    # the mover is not in a good state mark it as bad
	    enprint("%s in a %s state"%(self.format_name, self.server_state))
	    self.set_status(self.BADSTATUS[self.server_state])
            self.writemail("%s is in a %s state. Down Counter %s"%(self.format_name,
                                                                   self.server_state,
                                                           self.seen_down_d.get(self.format_name, 0)))
	else:
	    EnstoreServer.is_alive(self)
	    self.in_bad_state = 0


class UpDownInterface(generic_client.GenericClientInterface):
 
    def __init__(self, flag=1, opts=[]):
        self.do_parse = flag
        self.restricted_opts = opts
	self.summary = do_output
	self.no_mail = 0
	self.html = 0
	generic_client.GenericClientInterface.__init__(self)

    # define the command line options that are valid
    def options(self):
        if self.restricted_opts:
            return self.restricted_opts
        else:
            return self.help_options() + ["summary", "html", "no-mail"]

def do_real_work():
    sfile, outage_d, offline_d, seen_down_d = enstore_functions.read_schedule_file()

    summary_d = {enstore_constants.TIME: enstore_functions.format_time(time.time())}

    allowed_down_d = get_allowed_down_dict()

    # create all objects
    cs = ConfigServer(offline_d, seen_down_d, allowed_down_d)
    lcc = LogServer(cs, offline_d, seen_down_d, allowed_down_d)
    acc = AlarmServer(cs, offline_d, seen_down_d, allowed_down_d)
    ic = Inquisitor(cs, offline_d, seen_down_d, allowed_down_d)
    fcc = FileClerk(cs, offline_d, seen_down_d, allowed_down_d)
    vcc = VolumeClerk(cs, offline_d, seen_down_d, allowed_down_d)
    lib_man_d = cs.csc.get_library_managers({})
    library_managers = sortit(lib_man_d)

    meds = {}
    total_other_servers = []
    total_servers_names = []
    # do not look for servers that have the noupdown keyword in the config file
    for server in (cs, lcc, acc, ic, fcc, vcc):
	if server.noupdown == FALSE:
	    total_servers_names.append(server.name)
	    total_other_servers.append(server)

    total_lms = []
    total_movers = []
    for lm in library_managers:
	lm_name = lib_man_d[lm]['name']
        lmc = LibraryManager(cs, lm_name, offline_d, seen_down_d, allowed_down_d)
	if lmc.noupdown == FALSE:
	    total_lms.append(lmc) 
	    total_servers_names.append(lmc.name)

	# no duplicates in dict
	meds[cs.csc.get_media_changer(lm_name, lmc.timeout, lmc.tries)] = 1 
	movs = {}
	mov=cs.csc.get_movers(lm_name)
	for m in mov:
	    movs[(m['mover'])] = 1 # no duplicates in dictionary
	movers = sortit(movs)
        mover_objects = []
        for mov in movers:
            mvc = Mover(cs, mov, offline_d, seen_down_d, allowed_down_d)
	    if mvc.noupdown == FALSE:
		mover_objects.append(mvc)
		total_servers_names.append(mvc.name)
        lmc.movers = mover_objects
	lmc.num_movers = len(mover_objects)
        total_movers = total_movers + mover_objects
            
    media_changers = sortit(meds)

    for med in media_changers:
	if med:
	    mc = MediaChanger(cs, med, offline_d, seen_down_d, allowed_down_d)
	    if mc.noupdown == FALSE:
		total_other_servers.append(mc)
		total_servers_names.append(mc.name)

    total_servers = total_other_servers + total_movers + total_lms

    # we will get all of the info from the event relay.
    erc = event_relay_client.EventRelayClient()
    erc.start([event_relay_messages.ALIVE,])

    # event loop - wait for events
    start = time.time()
    got_one = 0          # used to measure if the event relay is up
    while 1:
	readable, junk, junk = select.select([erc.sock], [], [], 15)
	if not readable:
	    # timeout occurred - we will only wait a certain amount of
	    # time before giving up on listening for alive messages
	    if too_long(start):
		break
	    else:
		continue

	msg = erc.read()
	if msg and msg.server in total_servers_names:
	    total_servers_names.remove(msg.server)
	    got_one = 1
	    if enstore_functions.is_mover(msg.server):
		# we also got it's state in the alive msg, save it
		for mv in total_movers:
		    if msg.server == mv.name:
			mv.server_state = msg.opt_string
	    elif enstore_functions.is_library_manager(msg.server):
		# we also got it's state in the alive msg, save it
		for lm in total_lms:
		    if msg.server == lm.name:
			lm.server_state = msg.opt_string
	    if len(total_servers_names) == 0:
		# we have got em all
		break
	else:
	    # don't wait forever
	    if too_long(start):
		break
	    else:
		continue

    # close the socket
    erc.unsubscribe()
    erc.sock.close()

    # now, see what we have got
    for server in total_other_servers + total_movers:
	if not server.name in total_servers_names:
	    server.is_alive()
	else:
	    # server did not get back to us, assume it is dead
	    server.is_dead()

    # warnings need to be generated if more than 50% of a library_managers movers are down.
    for server in total_lms:
	if not server.name in total_servers_names:
	    server.is_alive()
	    state, bad_movers, ok_movers = server.mover_status()
	    if state == LOW_CAPACITY:
		enprint("LOW CAPACITY: Found, %s of %s movers not responding or in a bad state"%(bad_movers, 
									server.num_movers))
		server.writemail("Found LOW CAPACITY movers for %s"%(server.name,))
		server.status = enstore_constants.WARNING
		summary_d[server.name] = enstore_constants.WARNING
	    elif bad_movers != 0:
		enprint("Sufficient capacity of movers for %s, %s of %s responding"%(server.name,
										     ok_movers,
									           server.num_movers))
	else:
	    # server did not get back to us, assume it is dead
	    server.is_dead()

    # rewrite the schedule file as we keep track of how many times something has been down
    if sfile:
        # refresh data
	outage_d, offline_d, junk = sfile.read()
        # write it back with updated seen_down_d
	sfile.write(outage_d, offline_d, seen_down_d)

    # now figure out the state of enstore based on the state of the servers
    estate = enstore_constants.UP
    for server in total_servers:
	estate = server.get_enstore_state(estate)
	summary_d[server.format_name] = server.status
    else:
	summary_d[enstore_constants.ENSTORE] = enstore_state(estate)

    if summary_d[enstore_constants.ENSTORE] == enstore_constants.DOWN:
	stat = "DOWN"
	rtn = 1
    else:
	stat = "UP"
	rtn = 0

    # send summary mail if needed
    need_to_send = 0
    summary_file = tempfile.mktemp()
    subject = "Please check Enstore System (config node - %s)" % (cs.config_host,)
    os.system("echo ' Message from enstore_up_down.py:\n\n\tPlease check the full Enstore software system.\n\n" + \
              "See the Status-at-a-Glance Web Page\n\n' > %s"%(summary_file,))
    for server in total_servers:
        if server.mail_file:
            need_to_send = 1
            os.system('cat "%s" >> "%s"' % (server.mail_file, summary_file))
            server.remove_mail()
    if (not no_mail) and need_to_send:
	os.system("/usr/bin/Mail -s \"%s\" $ENSTORE_MAIL < %s"%(subject, summary_file))
    os.system("rm %s"%(summary_file,))
    
    enprint("Finished checking Enstore... system is defined to be %s"%(stat,))
    return (rtn, summary_d)

def do_work(intf):
    global prefix, do_output, no_mail

    # see if we are supposed to output well-formed html or not
    if intf.html:
	prefix = "<LI>"

    do_output = intf.summary
    no_mail = intf.no_mail

    rtn, summary_d = do_real_work()
    return (rtn)

if __name__ == "__main__" :

    # fill in interface
    intf = UpDownInterface()
 
    rtn = do_work(intf)
    sys.exit(rtn)
