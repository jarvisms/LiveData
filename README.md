# LiveModbusAPI

This application provides a basic CORS compatible JSON based web API to serve data directly from Modbus TCP devices such as meters. It was created to drive a live web display screen with data from Modbus based electricity meters to a javascript/html web page to show live power usage and Solar Panel generation. This can be seen as a permanent feature on a video wall at the Oculus building at the University of Warwick, UK where additional html and javascript has been implemented on top. The back end is driven by this app. Other use cases include live dashboards, data logging over the web or as the basis of a lightweight SCADA monitoring system.

The application uses basic configuration via a text/csv file and gives some minor caching and poll rate limiting to reduce the impact of flooding the modbus devices. It supports the same byte decoding/endian handling as the Python struct library and can apply a multiplicative scaling. It stores previous data as long as its different along with timestamps which would allow a downstream application to calculate recent rates of change for example. In the live display screen example above, this can be used to estimate consumption rate or power figure from a cumulative counter, instead of relying on the modbus device providing the actual rate/power directly. A background auto-update feature is also available which can update cached figures on a regular basis, particularly useful for the cumulative counters, otherwise, data retreival from the modbus device is done as and when API requests are made (provided its not bee too soon since the last request!).

The API can be called from other web sites and servers as it implements the Access Control headers as required for Cross Origin Resource Sharing (CORS).

## Prerequisites
This script was designed and proven to run on Python3.6 and later. It may run on earlier versions but this cannot be gauranteed. Only one external package is required which is modbus_tk. This can be obtained with: `pip intall modbus_tk` (or `pip3`)

## Configuration and use
Basic configuration is done via two files; `config.cfg` which sets up run time parameters for the application itself, and a second meterlist, defaulting to `Meter List.csv` which is where the list of modbus devices and their respective parameters are set up. The path to this file can be defined in the first configuration file.

## The Config File - config.cfg`
This must be located in the same folder as the script, but if it doesn't exist, it will be created with the following defaults.

### \[DEFAULT\]
The following settings define the general parameters for the application
- httpport = 8080 - *The TCP port which the app will serve using the http protocol.*
- httphost = localhost - *The hostname or IP to bind the server to. "0.0.0.0" will bind to everything on the machine*
- minpolltime = 1000 - *The minimum poll rate in millseconds to request data from Modbus devices. If web requests are repeated faster than this, clients will be given cached data to save flooding the modbus devices*
- meterlist = Meter List.csv - *The file that contains the definitions of the various API to modbus mappings, and related encoding parameters etc. See below*
- shutdowncmd = shutdown - *The special command that will allow the app to be shutdown via the web API. Using something obscure will reduce the likelihood of accidental or malicious shutdowns, although this does not eliminate the possibility*
- servefiles = false - *Defines whether or not the app will serve static files*
- listdirs = false - *Defines whether or not the app will list the contents of directories*
- fileroot = /path/to/public_html - *Defines the path to the root folder used to serve files or directories*
- autopollsec = 600 - *How often in seconds to automatically update the cached data in the background if required*

### \[RUNTIME\]
This section is added and updated by the app and should not be changed by users as its for information only
- starttime = 2018-07-25T22:34:51.667975 - *This will be the time the script began*
- pid = 8600 - *This will be the pid of the process which can be used to terminate*

## The Meter definition file
This is a csv file which can be created in Excel with the following headings:
`ID, Name, IP, Port, Address, Function, Register, Count, Encoding, BigEndian, Scale, Units, AutoUpdate`
Each row beneath defines a new mapping with the fields defines as follows:
- **ID** - This will be the API mapping ID which is case insensitive. Its recommended not to use spaces or characters not compatible with URLs
- **Name** - This is the free text name that will be returned in the JSON data.
- **IP** - The IP address, or optionally fully qualified domain name of the Modbus TCP Gateway
- **Port** - The Integer TCP port of the gateway, typically 502
- **Address** - The Integer slave address of the Modbus device itself
- **Function** - The Integer function number of the modbus request, for example 3 is for Read Holding Register
- **Register** - The Integer register address for the requested data
- **Count** - The Integer non zero number of 16 bit words to retreive from the device
- **Encoding** - Formatting string in the syntax of the Python Struct library to convert the raw binary data into a valid Python number
- **BigEndian** - True or False depending on whether the 16 bit words are big endian ordered or not
- **Scale** - A scale factor which is multiplied by the output before returning to requester. This can be a positive or negative integer or floating point number.
- **Units** - Free text to define the units of measurement. This is simply returned to the requester as is and can be considered optional.
- **AutoUpdate** - True or False depending on whether this parameter should be automatically updated frequently, on the timescale defined by the `autopollsec` option in the `config.cfg`

## Interacting with the API

# Getting data
Once the various files are set up, a HTTP GET request can be made to the `<httphost>:<httpport>` combination as defined in the configuration file with the url `/getdata?` followed by `id=<ID>` where `<ID>` refers to a mapping in the Meter definition file. All requests are case insensitive. Multiple such meters can be retreived simultaneously by having repeated ids listed seperated by ampersands. A full example is:
`http://localhost:8080/getdata?id=OculusGrid&id=OculusPV`
In this example, `OculusGrid` and `OculusPV` refer to the IDs defined in the Meter definition file.

The result will be be a JSON encoded object, similar to a Python dictionary with the requested IDs as keys, followed by another dictionary containing:
- **Name** : The text from the definition file
- **Value** : The numeric value of the data after decoding and scaling
- **Timestamp** : The javascript encoded timestamp of this request
- **ChangeTime** : The timestamp of when the value changed compared to the last value. This may be the same as the current timestamp
- **PrevValue** : The numeric value from the last request which was different
- **PrevChangeTime** : The timestamp of the last differing value
- **Units** : The text from the definition file
- **Status** : This will say *Cached* if the value is cached due to requests occuring faster than `minpolltime`, or *Polled* if the data returned is live, or this will give the Error message if the data retrieval failed.

An example output of the request above would be:
`{"oculusgrid": {"Name": "Grid Elec", "Value": 23.499000549316406, "Timestamp": "2018-12-22T15:52:00.580868", "ChangeTime": "2018-12-22T15:52:00.580872", "PrevValue": 23.57900047302246, "PrevChangeTime": "2018-12-22T15:51:58.916907", "Units": "KW", "Status": "Cached"}, "oculuspv": {"Name": "Solar PV", "Value": 0.01269999984651804, "Timestamp": "2018-12-22T15:52:01.924879", "ChangeTime": "2018-12-22T15:52:01.924883", "PrevValue": 0.012600000016391277, "PrevChangeTime": "2018-12-22T15:52:00.849968", "Units": "KW", "Status": "Polled"}}`

# Special commands

Instead of `/getdata?`, there is also a `/command?` interface with the following options

- **/command?reload** - This reloads the meter definition file allowing changes to be made without restarting the app
- **/command?listmeters** - This will list the meters currently configured
- **/command?status** - This will return another JSON object giving the process id (pid) of the app, the timestamp of when it first started, and the timestamp of this request
- **/command?\<shutdowncmd\>** - Where `<shutdowncmd>` is the text defined in the configuration file, this will cause the app to shutdown. This is always available and so should be set to something obscure.

## Using within a wider system
This app is intended to be run continuously and can be safely terminated by Ctrl+C, sending it a SIGTERM signal, or using the special shutdown command as mentioned above. Its advised to use a fully fledged web server such as Apache as a forwarding proxy infront of this app - this allows greater security and rate limiting flexibility and the opportunity to block access to special commands if this is a concern, or change the root path of the URL without having to rewrite this script.

The API is not intended to be invoked directly from a web browser, although this is possible and may prove useful for testing, but instead it would be invoked from some javascript, perhapse via an AJAX request to pull data into a web page for use there. The possible exception is when Directory and File serving is enabled, in which case the behaviour is as per Python's build in http.server module.

## Maintenance & Support
This project is solely maintained and supported by Mark Jarvis on a best effort basis. For help or to identify bugs, feel free to contact me or raise issues.
