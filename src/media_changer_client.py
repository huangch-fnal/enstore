###############################################################################
# src/$RCSfile$   $Revision$
#
#########################################################################
#                                                                       #
# Media Changer client.                                                 #
# Media Changer access methods                                          #
#                                                                       #
#########################################################################

# system imports
import pdb

#enstore imports
import configuration_client
import udp_client
import interface
import generic_client
import generic_cs
import Trace
import e_errors

class MediaChangerClient(generic_client.GenericClient):
    def __init__(self, csc=0, verbose=0, name="", \
                 host=interface.default_host(), \
                 port=interface.default_port()):
	self.print_id = "MEDCHC"
        self.media_changer=name
	self.verbose = verbose
        configuration_client.set_csc(self, csc, host, port, verbose)
        self.u = udp_client.UDPClient()
        ticket = self.csc.get(name)
	try:
            self.print_id = ticket['logname']
        except:
            pass

    # send the request to the Media Changer server and then send answer to user
    #      rcv_timeout is set to 60, the STK mnt/dismnt time is ~35 sec.   This
    #      should really be a function of which media changer we are talking to.
    def send (self, ticket, rcv_timeout=60, tries=0) :
        vticket = self.csc.get(self.media_changer)
        return  self.u.send(ticket, (vticket['hostip'], vticket['port']), rcv_timeout, tries)

    def loadvol(self, vol_ticket, drive):
        ticket = {'work'           : 'loadvol',
                  'vol_ticket' : vol_ticket,
                  'drive_id'       : drive
                  }
        return self.send(ticket)

    def unloadvol(self, vol_ticket, drive):
        ticket = {'work'           : 'unloadvol',
                  'vol_ticket' : vol_ticket,
                  'drive_id'       : drive
                  }
        return self.send(ticket)

class MediaChangerClientInterface(interface.Interface):
    def __init__(self):
        self.config_file = ""
        self.alive = 0
        self.alive_rcv_timeout = 0
        self.alive_retries = 0
        self.media_changer = ""
        self.volume = 0
        self.drive = 0
	self.got_server_verbose = 0
        interface.Interface.__init__(self)

        # parse the options
        self.parse_options()

    # define the command line options that are valid
    def options(self):
        return self.config_options() + self.verbose_options()+\
               ["config_file="] +\
               self.alive_options()+self.help_options()

    #  define our specific help
    def help_line(self):
        return interface.Interface.help_line(self)+" media_changer volume drive"

    # parse the options like normal but make sure we have other args
    def parse_options(self):
        interface.Interface.parse_options(self)
        if len(self.args) < 1 :
            self.print_help()
            sys.exit(1)
        else:
            self.media_changer = self.args[0]

        if (self.alive == 0) and (self.got_server_verbose == 0):
            # bomb out if we number of arguments is wrong
            if len(self.args) < 3 :
                self.print_help()
                sys.exit(1)
            else:
                self.volume = self.args[1]
                self.drive = self.args[2]


if __name__ == "__main__" :
    import sys
    Trace.init("medch cli")
    Trace.trace(1,"mcc called with args "+repr(sys.argv))

    # fill in the interface
    intf = MediaChangerClientInterface()

    # get a media changer client
    mcc = MediaChangerClient(0, intf.verbose, intf.media_changer, \
                            intf.config_host, intf.config_port)

    if intf.alive:
        ticket = mcc.alive(intf.alive_rcv_timeout,intf.alive_retries)
	msg_id = generic_cs.ALIVE
    elif intf.got_server_verbose:
        ticket = mcc.set_verbose(intf.server_verbose, intf.alive_rcv_timeout,\
	                         intf.alive_retries)
	msg_id = generic_cs.CLIENT
    else:
        ticket = mcc.unloadvol(intf.volume, intf.drive)
	mcc.enprint('unload returned:'+repr(ticket['status']))
	msg_id = generic_cs.CLIENT

    del mcc.csc.u
    del mcc.u		# del now, otherwise get name exception (just for python v1.5???)

    mcc.check_ticket(ticket, msg_id)
