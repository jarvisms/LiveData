#!/usr/bin/env python3
'''Live Data Web API for Modbus Meters
26 Aug 2018 - Version 1.3.1
	Implemented signal handler for SIGTERM shutdown
26 July 2018 - Version 1.3
	Implemented regular automatic polling for pulse meters and intial timestamping
13 June 2018 - Version 1.2
	Provides previous data for use with cumulative parameters
13 May 2018 - Version 1.1
	Refactored for Python 3, minor edits to docstrings
30 Oct 2016 - Version 1.0
	Initial complete version written for Python 2
'''
# Remember to change EOL convention to suit Windows or Linux depending on where this will run
import modbus_tk.modbus_tcp as modbus_tcp
import signal
from socketserver import ThreadingTCPServer
from http.server import SimpleHTTPRequestHandler
from threading import Lock, Event, Thread
from modbus_tk.modbus import ModbusError
from csv import DictReader
from time import sleep
from struct import pack, unpack, error
from datetime import datetime, timedelta
from json import dumps
from urllib.parse import urlsplit, parse_qs
from os import getcwd, chdir, getpid
from os.path import dirname, realpath
from configparser import SafeConfigParser

def LoadSettings():
	'''Open the meter definition file defined in the config file, convert all of the data to suitable data types and store everything in a dictionary'''
	global meters
	with open(config.get('DEFAULT','meterlist'), "r", newline='') as meterfile:
		metercsv = DictReader(meterfile, dialect="excel")
		backup = meters.copy()	# Back this up incase this load doesn't work
		meters.clear()	# Clear any existing data ready for new
		try:
			for item in metercsv:	# Each line should be a dictionary of strings
				item.update({		# Convert strings to appropriate data types wth default (possibly invalid) values. Items which are strings are left alone.
				'Function':int(item.get('Function',0)),	# This is actually an invalid function code and will raise a Modbus Exception
				'Count':int(item.get('Count',0)),		# Again, this is an invalid default
				'Register':int(item.get('Register',0)),
				'Address':int(item.get('Address',0)),	# Invalid default
				'Port':int(item.get('Port',0)),			# Invalid default
				'Scale':float(item.get('Scale',1)),
				'Value':float(item.get('Value',0)),
				'PrevValue':float(item.get('PrevValue',0)),
				'Timestamp':item.get('Timestamp',datetime.min),	# Default is the dawn of time
				'PrevChangeTime':item.get('PrevChangeTime',datetime.min),	# Default is the dawn of time
				'ChangeTime':item.get('ChangeTime',datetime.min),	# Default is the dawn of time
				'ThreadLock':Lock()	# Thread Lock so only one thread tries to grab new data for any one meter
				})
				BigEndian = item.get('BigEndian','True').lower()
				if BigEndian in ['>','big','true','1','yes', '']:	# These, including blank, signify BigEndian encoding
					item.update({'BigEndian':True})
				elif BigEndian in ['<','little','false','0','no']:	# These signify LittleEndian encoding
					item.update({'BigEndian':False})
				else:
					raise ValueError
				AutoUpdate = item.get('AutoUpdate','False').lower()
				if AutoUpdate in ['true','1','yes']:	# These signify AutoUpdate is required
					item.update({'AutoUpdate':True})
				elif AutoUpdate in ['false','0','no', '']:	# These, including blank signify AutoUpdate is not required
					item.update({'AutoUpdate':False})
				else:
					raise ValueError
				meters.update({item.pop('ID').lower(): item})	# Key to the dictionary is the ID itself
		except ValueError as err:
			print("The config file had incorrect data in it: {}\nOffending line: {}".format(err,item))
			meters = backup.copy()	# Put the backup data back
			del(backup)

def GoModbus(id):
	'''Check to see if the requested meter's data is in cache and still valid and if not, retrieve it, decode it and return a dictionary'''
	global meters	# Ensures the dictionary is accessible
	if ((datetime.utcnow() - meters[id]['Timestamp']) > timedelta(0,0,0,config.getint('DEFAULT','minpolltime')) and meters[id]['ThreadLock'].acquire(False)):
		try:	# If the cached data is stale, and there is no Thread retreiving new data (holding the lock), try and get new data
			master = modbus_tcp.TcpMaster(host=meters[id].get('IP','127.0.0.1'), port=meters[id].get('Port',502))	# Sets up a TCP connection to the given slave
			result = master.execute(meters[id].get('Address',0), meters[id].get('Function',0), meters[id].get('Register',0), meters[id].get('Count',0)) # Polls for the data
			master._do_close()	# Closes the connection again
			val = meters[id]['Scale'] * unpack('>'+meters[id].get('Encoding','>'+'H'*len(result)), pack('>'+'H'*len(result), *(result if meters[id]['BigEndian'] else reversed(result))))[0]	# Orders the raw 16 bit words depending on Endianness, and re-encodes them in the given data format
			if val != meters[id]['Value']:	# Proceed only if the new value is different to the old (Cumulative count values must change)
				meters[id].update({'PrevValue' : meters[id]['Value'], 'PrevChangeTime' : meters[id]['ChangeTime'] , 'Value' : val, 'Timestamp':datetime.utcnow(), 'ChangeTime': datetime.utcnow() })	# Stores original data as previous, and stores fresh data along with the timestamp of this fresh data, the LastChange records when the change was seen
				status = 'Polled'
				if meters[id]['PrevChangeTime'] == datetime.min and meters[id]['PrevValue'] == 0:
					meters[id]['PrevValue'] = meters[id]['Value']	# This looks like a repeat of above but the Value has since been updated
			else:	# If the cumulative values have not moved (or by chance, instantaneous values are the same)
				meters[id]['Timestamp']=datetime.utcnow()	# Just store the timestamp as the value is unchanged as is LastChange. Preserve the previous details
				status = 'Polled but no recent change'
		except ModbusError as e:	# Catch Modbus Specific Exceptions, likely invalid registers etc. Returns the potentially stale cached data
			print("Modbus error ", e.get_exception_code())
			status = 'Modbus Error {}'.format(e.get_exception_code())
		except Exception as e2:	# Catch all other Exceptions, likely socket timeouts or wrong encoding specified etc. Returns the potentially stale cached data
			print("Error ", str(e2))
			status = 'Error {}'.format(str(e2))
		finally:
			meters[id]['ThreadLock'].release()	# Ensure the lock is released no matter what
	else:	# If the cached data is not stale, then don't do anything and just return that instead
		status = 'Cached'
	return {'Name':meters[id]['Name'], 'Value':meters[id]['Value'], 'Timestamp':meters[id]['Timestamp'].isoformat(), 'ChangeTime':meters[id]['ChangeTime'].isoformat(), 'PrevValue':meters[id]['PrevValue'], 'PrevChangeTime':meters[id]['PrevChangeTime'].isoformat(), 'Units':meters[id]['Units'], 'Status':status}

class CustomHandler(SimpleHTTPRequestHandler):	# Based on Python Standard Library
	global meters
	def do_GET(self):	# Handles HTTP GET Verb
		UrlSplit = urlsplit(self.path.lower())	# Drop it all to lower case and splits the URL and Query parts.
		QuerySplit = parse_qs(UrlSplit.query)	# Splits apart the parameters and variables into a dictionary
		if UrlSplit.path == "/getdata" and "id" in QuerySplit and set(QuerySplit.get('id',[])) <= set(meters.keys()):
			self.send_response(200)	# Request must be exactly the right API path, be asking for the right parameter and the ID must be valid
			self.send_header('Access-Control-Allow-Origin', '*')
			self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
			self.send_header("Access-Control-Allow-Headers", "X-Requested-With")
			self.send_header("Access-Control-Allow-Headers", "Content-Type")
			self.send_header('Content-Type','application/json')
			self.end_headers()	# CORS compatible headers given
			Data = {id:GoModbus(id) for id in QuerySplit['id']}	# Iterates over all the IDs requested
			self.wfile.write(dumps(Data).encode())	# Returns the data as JSON format
			return
		elif UrlSplit.path == "/command" and UrlSplit.query == 'reload':	# /command?reload specifically
			self.send_response(202)	# Accepted
			self.send_header('Content-Type','text/html')
			self.end_headers()
			self.wfile.write(b"Reloading Meter List...\n")
			print("Reloading Meter List...")
			LoadSettings()	# Tries to reload
			self.wfile.write(b"Done")	# It will currently say Done whether it was successful or not
			print("Done")
			return
		elif UrlSplit.path == "/command" and UrlSplit.query == 'listmeters':	# /command?listmeters specifically
			self.send_response(200)
			self.send_header('Content-Type','application/json')
			self.end_headers()
			self.wfile.write(dumps(list(meters.keys())).encode())	# JSON format of the valid IDs
			return
		elif UrlSplit.path == "/command" and UrlSplit.query == 'status':	# /command?status specifically
			self.send_response(200)
			self.send_header('Content-Type','application/json')
			self.end_headers()
			self.wfile.write(dumps({'pid':getpid(),'starttime':starttime.isoformat(),'utcnow':datetime.utcnow().isoformat()}).encode())	# Give local Process ID and Working Directory where files are served from
			return
		elif UrlSplit.path == "/command" and UrlSplit.query == config.get('DEFAULT','shutdowncmd'):	# shutdown string is defined in the config file and can be obscure
			self.send_response(202)	# Accepted
			self.send_header('Content-Type','text/html')
			self.end_headers()
			self.wfile.write(b"Shutting Down...\n")
			Killer(0,None)
			self.wfile.write(b"Done")	# Hasn't technically shutdown yet but the serving thread should be terminating imminently
			return
		elif config.getboolean('DEFAULT','servefiles'):	# If serving files is allowed, then the original Python library does this.
			SimpleHTTPRequestHandler.do_GET(self)
		else:
			self.send_error(404)	# File not Found for anything else.
			return
	def do_OPTIONS(self):	# This is purely for the CORS Preflight and is only given on valid API data requests.
		UrlSplit = urlsplit(self.path.lower())
		QuerySplit = parse_qs(UrlSplit.query)
		if UrlSplit.path == "/getdata" and "id" in QuerySplit and set(QuerySplit.get('id',[])) <= set(meters.keys()):
			self.send_response(200, "OK")	# Request must be exactly the right API path, be asking for the right parameter and the ID must be valid
			self.send_header('Access-Control-Allow-Origin', '*')
			self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
			self.send_header("Access-Control-Allow-Headers", "X-Requested-With")
			self.send_header("Access-Control-Allow-Headers", "Content-Type")
			self.end_headers()
			return
		else:
			self.send_error(405)	# Method not allowed
			return
	def list_directory(self, path):	# Patches the list_directory method so that files an be served but directories not listed.
		if config.getboolean('DEFAULT','servefiles') and config.getboolean('DEFAULT','listdirs'):
			SimpleHTTPRequestHandler.list_directory(self, path)	# If listing is allowed, then do what the original method does
		else:
			self.send_error(403) #No permission to list directory
			return None	# Effectively, all directory listing is blocked

def RegularUpdate(meters, wait, Shutdown):
	while not Shutdown.is_set():
		for id in meters:
			if meters[id]['AutoUpdate']:
				print(f'Auto-Update meter {meters[id]["Name"]}:{GoModbus(id)["Timestamp"]}')	# Only on meters with AutoUpdate enabled, refresh cached data every so often.
		sleep(wait)

def Killer(signum,frame):
	print("Shutting down...")
	httpd.shutdown()
	Shutdown.set()
	print("Done")

if __name__ == '__main__':
	starttime = datetime.utcnow()
	config = SafeConfigParser({'httpport':'8080', 'httphost':'localhost', 'minpolltime':'1000', 'meterlist':'Meter List.csv', 'shutdowncmd':'shutdown', 'servefiles':'false', 'listdirs':'false'})	# Default configuration
	with open(dirname(realpath(__file__))+'/config.cfg', 'r+') as configfile:
		config.read_file(configfile)
		if not config.has_section('RUNTIME'):
			config.add_section('RUNTIME')
		config.set('RUNTIME','starttime',starttime.isoformat())
		config.set('RUNTIME','pid',str(getpid()))
		configfile.seek(0)
		config.write(configfile)
		configfile.truncate()
	cwd = getcwd()	# Store the working directory when the script began
	if config.getboolean('DEFAULT','servefiles'):
		nwd = config.get('DEFAULT','fileroot')
		print('Files and Folders will be visible from root folder: "{}"'.format(nwd))
		chdir(nwd)	# If files are to be served, change the working directory to the document root folder
	meters=dict()
	LoadSettings() # Initial load of definition file
	Shutdown = Event()	# The flag which will shutdown the auto-updating thread
	AutoPoll = Thread(target=RegularUpdate, args=(meters, config.getfloat('DEFAULT','autopollsec'), Shutdown), name='AutoPollThread', daemon=True)	# Daemon Thread to auto-update certain items
	AutoPoll.start()
	httpd = ThreadingTCPServer((config.get('DEFAULT','httphost'), config.getint('DEFAULT','httpport')),CustomHandler)	# Start the HTTP Server
	print('Server Running "{}:{}"'.format(config.get('DEFAULT','httphost'),config.get('DEFAULT','httpport')))
	print('To shut down, visit "/command?{}"'.format(config.get('DEFAULT','shutdowncmd')))
	signal.signal(signal.SIGTERM,Killer)
	try:
		httpd.serve_forever()
	except KeyboardInterrupt:	# Allow Ctrl+C locally to close it gracefully
		Killer(0,None)	# Envoke the signal handler
	httpd.server_close()	# Finally close everything off
	chdir(cwd)	# Change the working directory back to what it was when it started.
	raise SystemExit	# Ensure explicit termination at this point
