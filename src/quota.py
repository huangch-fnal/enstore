#!/usr/bin/env python

import option
import configuration_client
import pg
import info_client
import generic_client
import log_client
import sys
import string
import os
import pwd
import Trace
import e_errors

MY_NAME = "QUOTA"
MY_SERVER = None

# take a query object and pretty print the results
def show_query_result(res):

	# determine the format
	width = []
	fields = res.listfields()
	w = len(fields)
	for i in range(w):
		width.append(len(fields[i]))

	result = res.getresult()
	for r in result:
		for i in range(w):
			l1 = len(str(r[i]))
			if l1 > width[i]:
				width[i] = l1

	format = []
	for i in range(w):
		format.append("%%%ds "%(width[i]))

	# determine the length of the separation line
	ll = 0
	for i in range(w):
		ll = ll + width[i]
	ll = ll + 2*(w - 1)

	# print the header
	for i in range(w):
		print format[i]%(fields[i]),
	print
	print "-"*ll

	# print the rows
	for r in result:
		for i in range(w):
			print format[i]%(r[i]),
		# mark if the numbers are not quite right
		if r[-1] > r[-2] or r[-2] > r[-3]:
			print "*"
		else:
			print

# try to identify the user using LOGNAME and kerberos principal
def whoami():
	# find the principal of kerberos ticket
	kmsg = os.popen("klist").readlines()
	kk = 'unknown'
	for i in kmsg:
		if i[:18] == "Default principal:":
			kk = string.split(i)[2]
			break
	logname = 'unknown'
	# try to find the real user name through $LOGNAME
	if os.environ.has_key('LOGNAME'):
		logname = os.environ['LOGNAME']
	else:
		# if failed, use effective user name
		logname = pwd.getpwuid(os.getuid())[0]

	return "%s(%s)"%(logname, kk)

# handles everthing with quota		
class Quota:
	def __init__(self, csc):
		# where is the database
		self.csc = configuration_client.ConfigurationClient(csc)
		dbInfo = self.csc.get('database')
		self.host = dbInfo['db_host']
		self.port = dbInfo['db_port']
		self.dbname = dbInfo['dbname']
		self.db = pg.DB(host=self.host, port=self.port, dbname=self.dbname)
		self.uname = whoami()

	# informational log any way, stick user identity before the msg
	def log(self, m):
		Trace.log(e_errors.INFO, self.uname+' '+m)

	# this is not used any more but it's handy to be kept around
	def show_all(self):
		res = self.db.query("select * from quota order by library;")
		show_query_result(res)

	# show summary by the libraries
	def show_by_library(self):
		q = "select library, sum(requested) as requested, \
			sum(authorized) as authorized, \
			sum(quota) as quota from quota \
			group by library order by library;"
		show_query_result(self.db.query(q))

	# show [library [storage_group]]
	def show(self, library=None, sg=None):
		q = "select value from option where key = 'quota';"
		state = self.db.query(q).getresult()[0][0]
		print "QUOTA is %s\n"%(string.upper(state))
		if library:
			# with specific library
			if sg:
				# with specific storage group
				q = "select library, storage_group, \
					requested, authorized, quota \
					from quota where \
					library = '%s' and \
					storage_group = '%s';"%(
					library, sg)
			else:
				# without specific storage group
				q = "select library, storage_group, \
					requested, authorized, quota \
					from quota where \
					library = '%s' \
					order by storage_group;"%(
					library)
		else:
			# without specific library -- show all
			q = "select library, storage_group, requested,\
				authorized, quota from quota \
				order by library, storage_group;"

		show_query_result(self.db.query(q))

	# check if the numbers make sense
	def check(self, library, sg):
		msg = ""
		q = "select * from quota where library = '%s' and \
			storage_group = '%s';"%(library, sg)
		res = self.db.query(q).dictresult()
		if len(res):
			if res[0]['authorized'] > res[0]['requested']:
				msg = msg+"authorized(%d) > requested(%d)! "%(res[0]['authorized'], res[0]['requested'])
			if res[0]['quota'] > res[0]['authorized']:
				msg = msg+"quota(%d) > authorized(%d)!"%(res[0]['quota'], res[0]['authorized'])
			if msg:
				print "Warning:", msg

	# check if (library, sg) already exist
	def exist(self, library, sg):
		q = "select * from quota where library = '%s' and \
			storage_group = '%s';"%(library, sg)
		return self.db.query(q).ntuples()

	# create a new (library, storage_group)
	def create(self, library, sg, requested = 0, authorized = 0,
		quota = 0):
		# check if it already existed
		if self.exist(library, sg):
			print "('%s', '%s') already exists."%(library, sg)
			return

		q = "insert into quota values('%s', '%s', %d, %d, %d);"%(
			library, sg, requested, authorized, quota)
		self.db.query(q)
		self.show(library, sg)
		self.check(library, sg)
		msg = "('%s', '%s', %d, %d, %d) created"%(
			library, sg, requested, authorized, quota)
		self.log(msg)
	# delete (library, sg)
	def delete(self, library, sg):
		# check if it already existed
		if self.exist(library, sg):
			q = "delete from quota where library = '%s' and \
				storage_group = '%s';"%(library, sg)
			self.db.query(q)
			msg = "('%s', '%s') deleted"%(library, sg)
			self.log(msg)
		else:
			print "('%s', '%s') does not exist."%(library, sg)


	# set requested for (library, sg)
	def set_requested(self, library, sg, n):
		# check if it already existed
		if self.exist(library, sg):
			q = "update quota set requested = %d where \
				library = '%s' and \
				storage_group = '%s';"%(n, library, sg)
			self.db.query(q)
			self.show(library, sg)
			self.check(library, sg)
			msg = "set requested of ('%s', '%s') to %d"%(
				library, sg, n)
			self.log(msg)
		else:
			print "('%s', '%s') does not exist."%(library, sg)

	# set authorized for (library, sg)
	def set_authorized(self, library, sg, n):
		# check if it already existed
		if self.exist(library, sg):
			q = "update quota set authorized = %d where \
				library = '%s' and \
				storage_group = '%s';"%(n, library, sg)
			self.db.query(q)
			self.show(library, sg)
			self.check(library, sg)
			msg = "set authorized of ('%s', '%s') to %d"%(
				library, sg, n)
			self.log(msg)
		else:
			print "('%s', '%s') does not exist."%(library, sg)

	# set quota for (library, sg)
	def set_quota(self, library, sg, n):
		# check if it already existed
		if self.exist(library, sg):
			q = "update quota set quota = %d where \
				library = '%s' and \
				storage_group = '%s';"%(n, library, sg)
			self.db.query(q)
			self.show(library, sg)
			self.check(library, sg)
			msg = "set quota of ('%s', '%s') to %d"%(
				library, sg, n)
			self.log(msg)
		else:
			print "('%s', '%s') does not exist."%(library, sg)

	# enable quota
	def enable(self):
		q = "select value from option where key = 'quota';"
		res = self.db.query(q).getresult()
		if res:
			state = res[0][0]
			if state == 'enabled':
				return
			q = "update option set value = 'enabled' where key = 'quota';"
		else:
			q = "insert into option (key, value) values ('quota', 'enabled');"
		self.db.query(q)
		self.log("quota enabled")

	# disable quota
	def disable(self):
		q = "select value from option where key = 'quota';"
		res = self.db.query(q).getresult()
		if res:
			state = res[0][0]
			if state == 'disabled':
				return
			q = "update option set value = 'disabled' where key = 'quota';"
		else:
			q = "insert into option (key, value) values ('quota', 'disabled');"
		self.db.query(q)
		self.log("quota disabled")

	# quota_enabled() is not used directly.
	# it serves a prototype for the real one in volume_clerk.py
	# it is backward compatible with old quota_enabled()
	def quota_enabled(self):
		q = "select value from option where key = 'quota';"
		state = self.db.query(q).getresult()[0][0]
		if state != "enabled":
			return None
		q = "select library, storage_group, quota, significance from quota;"
		res = self.db.query(q).dictresult()
		libraries = {}
		order = {'bottom':[], 'top':[]}
		for i in res:
			if not libraries.has_key(i['library']):
				libraries[i['library']] = {}
			libraries[i['library']][i['storage_group']] = i['quota']
			if i['significance'] == 'y':
				order['top'].append((i['library'], i['storage_group']))
			else:
				order['bottom'].append((i['library'], i['storage_group']))
		q_dict = {
			'enabled': 'yes',
			'libraries': libraries,
			'order': order
		}

		return q_dict
				
class Interface(option.Interface):
	def __init__(self, args=sys.argv, user_mode=0):
		self.show = None
		self.storage_group = None
		self.show_by_library = None
		self.set_requested = None
		self.set_authorized = None
		self.set_quota = None
		self.create = None
		self.requested = 0
		self.authorized = 0
		self.quota = 0
		self.number = 0
		self.delete = None
		self.enable = None
		self.disable = None

		option.Interface.__init__(self, args=args, user_mode=user_mode)

	def valid_dictionaries(self):
		return (self.help_options, self.quota_options)

	quota_options = {
		option.SHOW:{
			option.HELP_STRING: "show quota",
			option.VALUE_TYPE: option.STRING,
			option.VALUE_USAGE: option.OPTIONAL,
			option.VALUE_LABEL: "library",
			option.DEFAULT_VALUE: "-1",
			option.USER_LEVEL: option.ADMIN,
			option.EXTRA_VALUES: [{
				option.VALUE_NAME: "storage_group",
				option.VALUE_TYPE: option.STRING,
				option.VALUE_USAGE: option.OPTIONAL,
				option.DEFAULT_TYPE: None,
				option.DEFAULT_VALUE: None
			}] },
		option.SHOW_BY_LIBRARY:{
			option.HELP_STRING: "show quota by the libraries",
			option.VALUE_TYPE: option.STRING,
			option.VALUE_USAGE: option.IGNORED,
			option.DEFAULT_TYPE:option.INTEGER,
			option.DEFAULT_VALUE:option.DEFAULT,
			option.USER_LEVEL:option.ADMIN},
		option.SET_REQUESTED:{
			option.HELP_STRING: "set requested number for (library, storage_group)",
			option.VALUE_TYPE: option.STRING,
			option.VALUE_USAGE: option.REQUIRED,
			option.VALUE_LABEL: "library",
			option.USER_LEVEL: option.ADMIN,
			option.EXTRA_VALUES: [{
				option.VALUE_NAME: "storage_group",
				option.VALUE_TYPE: option.STRING,
				option.VALUE_USAGE: option.REQUIRED}, {
				option.VALUE_NAME: "number",
				option.VALUE_TYPE: option.INTEGER,
				option.VALUE_USAGE: option.REQUIRED
			}] },
		option.SET_AUTHORIZED:{
			option.HELP_STRING: "set authorized number for (library, storage_group)",
			option.VALUE_TYPE: option.STRING,
			option.VALUE_USAGE: option.REQUIRED,
			option.VALUE_LABEL: "library",
			option.USER_LEVEL: option.ADMIN,
			option.EXTRA_VALUES: [{
				option.VALUE_NAME: "storage_group",
				option.VALUE_TYPE: option.STRING,
				option.VALUE_USAGE: option.REQUIRED}, {
				option.VALUE_NAME: "number",
				option.VALUE_TYPE: option.INTEGER,
				option.VALUE_USAGE: option.REQUIRED
			}] },
		option.SET_QUOTA:{
			option.HELP_STRING: "set quota for (library, storage_group)",
			option.VALUE_TYPE: option.STRING,
			option.VALUE_USAGE: option.REQUIRED,
			option.VALUE_LABEL: "library",
			option.USER_LEVEL: option.ADMIN,
			option.EXTRA_VALUES: [{
				option.VALUE_NAME: "storage_group",
				option.VALUE_TYPE: option.STRING,
				option.VALUE_USAGE: option.REQUIRED}, {
				option.VALUE_NAME: "number",
				option.VALUE_TYPE: option.INTEGER,
				option.VALUE_USAGE: option.REQUIRED
			}] },
		option.CREATE:{
			option.HELP_STRING: "create quota for (library, storage_group)",
			option.VALUE_TYPE: option.STRING,
			option.VALUE_USAGE: option.REQUIRED,
			option.VALUE_LABEL: "library",
			option.USER_LEVEL: option.ADMIN,
			option.DEFAULT_VALUE: "hello",
			option.EXTRA_VALUES: [{
				option.VALUE_NAME: "storage_group",
				option.VALUE_TYPE: option.STRING,
				option.VALUE_USAGE: option.REQUIRED}, {
				option.VALUE_NAME: "requested",
				option.VALUE_TYPE: option.INTEGER,
				option.DEFAULT_VALUE: 0,
				option.VALUE_USAGE: option.OPTIONAL}, {
				option.VALUE_NAME: "authorized",
				option.VALUE_TYPE: option.INTEGER,
				option.DEFAULT_VALUE: 0,
				option.VALUE_USAGE: option.OPTIONAL}, {
				option.VALUE_NAME: "quota",
				option.VALUE_TYPE: option.INTEGER,
				option.DEFAULT_VALUE: 0,
				option.VALUE_USAGE: option.OPTIONAL
			}] },
		option.DELETE:{
			option.HELP_STRING: "delete (library, storage_group)",
			option.VALUE_TYPE: option.STRING,
			option.VALUE_USAGE: option.REQUIRED,
			option.VALUE_LABEL: "library",
			option.USER_LEVEL: option.ADMIN,
			option.EXTRA_VALUES: [{
				option.VALUE_NAME: "storage_group",
				option.VALUE_TYPE: option.STRING,
				option.VALUE_USAGE: option.REQUIRED
			}] },
		option.ENABLE:{
			option.HELP_STRING: "enable quota",
			option.DEFAULT_VALUE:option.DEFAULT,
			option.DEFAULT_TYPE:option.INTEGER,
			option.VALUE_USAGE:option.IGNORED,
			option.USER_LEVEL: option.ADMIN},
		option.DISABLE:{
			option.HELP_STRING: "disable quota",
			option.DEFAULT_VALUE:option.DEFAULT,
			option.DEFAULT_TYPE:option.INTEGER,
			option.VALUE_USAGE:option.IGNORED,
			option.USER_LEVEL: option.ADMIN},
			
	}

def do_work(intf):
	q = Quota((intf.config_host, intf.config_port))
	logc = log_client.LoggerClient(q.csc)
	Trace.init(string.upper(MY_NAME))

	if intf.show:
		if intf.show == "-1":
			q.show()
		else:
			if intf.storage_group:
				q.show(intf.show, intf.storage_group)
			else:
				q.show(intf.show)
	elif intf.show_by_library:
		q.show_by_library()
	elif intf.create:
		if intf.requested == 'None':
			intf.requested = 0
		if intf.authorized == 'None':
			intf.authorized = 0
		if intf.quota == 'None':
			intf.quota = 0
		q.create(intf.create, intf.storage_group,
			intf.requested, intf.authorized, intf.quota)
	elif intf.set_requested:
		q.set_requested(intf.set_requested, intf.storage_group,
			intf.number)
	elif intf.set_authorized:
		q.set_authorized(intf.set_authorized, intf.storage_group,
			intf.number)
	elif intf.set_quota:
		q.set_quota(intf.set_quota, intf.storage_group,
			intf.number)
	elif intf.delete:
		q.delete(intf.delete, intf.storage_group)
	elif intf.disable:
		q.disable()
	elif intf.enable:
		q.enable()

if __name__ == '__main__':
	intf = Interface(user_mode=0)
	do_work(intf)
