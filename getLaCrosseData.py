import cgi
import os
import urllib
import logging
import datetime
import json
import re

from google.appengine.api import users
from google.appengine.ext import ndb
from google.appengine.api import urlfetch
from google.appengine.api import taskqueue

import jinja2
import webapp2
from connect import *

JINJA_ENVIRONMENT = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)

# the write rate should be limited to ~1/second.
def sensor_owner_key(sensor_owner_email):
  return ndb.Key('SensorOwner', sensor_owner_email)

def sensor_key(sensor_owner_key, sensor_serial):
  """Constructs a Datastore key for a sensor entity with sensor_serial."""
  return ndb.Key('Sensor', sensor_serial, parent = sensor_owner_key)

def observation_key( sensor_key, timestamp):
  return ndb.Key('Observation', timestamp, parent = sensor_key )

class SensorOwner(ndb.Model):
  '''Models a sensor owner'''
  name = ndb.StringProperty()
  
class Sensor(ndb.Model):
  '''Models an individual sensor'''
  name = ndb.StringProperty()
  url=ndb.StringProperty()
  first_observation_date=ndb.DateTimeProperty()
  last_observation_date=ndb.DateTimeProperty()
  observation_count=ndb.IntegerProperty()  
  
class Observation(ndb.Model):
  """Models an individual observation data point entry."""
  '''The observation key is the timestamp'''
  date_time = ndb.DateTimeProperty()
  temperature_1 = ndb.FloatProperty()
  temperature_2 = ndb.FloatProperty()
  humidity = ndb.FloatProperty()
  low_battery = ndb.FloatProperty()
  link_quality = ndb.FloatProperty()

class MainPage(webapp2.RequestHandler):

    def get(self):

      sensor_query = Sensor.query()
      sensors = sensor_query.fetch()
      sensor_data = []

      template_values = {
              'sensors': sensors,
      }

      template = JINJA_ENVIRONMENT.get_template('index.html')
      self.response.write(template.render(template_values))

class New_Sensor_Handler(webapp2.RequestHandler):

    def get(self):
      owner_key = sensor_owner_key('xtopher.brandt@gmail.com')
      sensor_owner_query = SensorOwner.query(SensorOwner.key==owner_key)
      sensor_owners = sensor_owner_query.fetch()

      '''if the sensor owner does not exist then add me'''
      if len(sensor_owners) == 0 :
        sensor_owner = SensorOwner()
        sensor_owner.key = owner_key
        sensor_owner.name = 'Christopher Brandt'
        sensor_owner.put()
      
      '''Get the set of sensor that this owner has'''
      sensors_query = Sensor.query(ancestor=owner_key)
      sensors = sensors_query.fetch()

      '''if the sensor owner does not have a sensor then continue to fetch everything'''
      '''not elegant but does the job to ensure we don't duplicate effort'''
      if len(sensors) == 0 :

        sensorData = SensorData()
        sensorData.login()
        sensorData.sync()

      else:
        '''otherwise abort'''
        logging.info ( "Sensor exists so aborting new sensor")
        
      self.redirect('/')

class Sensor_Handler(webapp2.RequestHandler):
  
  def get(self, name):
      sensor_query = Sensor.query(Sensor.name == name)
      sensors = sensor_query.fetch()
      sensor_data = []
      
      if len(sensors) > 0 :
        sensor_unit = sensors[0]
        logging.info ('Sensor : {0}'.format(sensor_unit.key))

        sensor_data_query = Observation.query(ancestor=sensor_unit.key).order(-Observation.date_time)
        sensor_data = sensor_data_query.fetch()

        logging.info ('found ' + str(len(sensor_data)) + ' data points')

      template_values = {
              'data_points': sensor_data,
      }

      template = JINJA_ENVIRONMENT.get_template('sensor_data.html')
      self.response.write(template.render(template_values))
    
'''
  Synchronize a sensor Handler
    Query string parameters:
      start := earliest date in the synchronization period
      end := latest date in the synchronization period
      
      if niether start nor end are specified then the period is simply the last observation to today
      if the start is provided without an end then the period is the start to today
      if the end is provided with no start then the period is the beginning of time to the end.
'''
class Synchronize(webapp2.RequestHandler):

    def get(self, name):
      sensor_unit = None
      sensor_query = Sensor.query(Sensor.name == name)
      sensors = sensor_query.fetch()
      
      if len(sensors) > 0 :
        sensor_unit = sensors[0]
        logging.info ('Synchronizing Sensor : {0}'.format(sensor_unit.key))
      
      sensorData = SensorData(sensor_unit)
      sensorData.login()
      
      requestStart = None
      requestEnd = None
      if len (self.request.get('start')) > 0 :
        requestStart = datetime.datetime.strptime(self.request.get('start'), '%Y-%m-%d')
 
      if len (self.request.get('end')) :  
        requestEnd = datetime.datetime.strptime(self.request.get('end'), '%Y-%m-%d')
      
      sensorData.sync(requestStart,requestEnd)

      self.redirect('/')

class SensorData:
    
    session_cookie = ''
    sensor = None
    
    def __init__(self, sensor = None):
      self.sensor = sensor
      
    def login(self):
            
      base_url = "https://www.lacrossealerts.com"
      form_fields = {
        "username": "xtopher.brandt@gmail.com",
        "password": "SRTTy7ZvtSWa5DhXKuY2"
      }
      form_data = urllib.urlencode(form_fields)
      
      result = urlfetch.fetch(base_url + '/login',
                              payload=form_data,
                              method=urlfetch.POST,
                              headers={'Content-Type': 'application/x-www-form-urlencoded'},
                              follow_redirects=False)

      logged_in = False
      last_redirect = '';
      
      if result.headers['set-cookie']:
        for cookie_parts in result.header_msg.getheaders('set-cookie') :
            self.session_cookie = cookie_parts.split(';')[0]
      
      while result.status_code == 302 :
              logging.info ( 'redirect : ' + result.headers['location']  )
              last_redirect = result.headers['location']
              result = urlfetch.fetch(base_url + result.headers['location'],
                                      method=urlfetch.POST,
                                      headers={'Cookie' : self.session_cookie},
                                      follow_redirects=False)

      if last_redirect == '/devices' :
         logged_in = True
         logging.info( 'Logged In' ) 
 
    '''Handles the protocol for getting the most detailed data from Lacrosse'''
    def sync(self, requestStart=None, requestEnd=None):
          
      last_observation = datetime.datetime(1970,11,7,0,0,0)

      if self.sensor is not None :
        last_observation = self.sensor.last_observation_date

      '''Unless otherwise specified the first day to sync is the last observation and the last day is today'''
      if requestStart is None and requestEnd is None:
        requestStart = last_observation
        requestEnd = datetime.datetime.now()
      
      '''if an end date is specified with no start, then start at the beginning of time and go to the specified end'''
      if requestStart is None and requestEnd is not None :
        requestStart = datetime.datetime(1970,11,7,0,0,0)
      
      '''if a start date is specified with no end date, then start at the specified date and go to today'''
      if requestStart is not None and requestEnd is None :
        requestEnd = datetime.datetime.now()
        
      if requestStart is not None and requestEnd is not None :
        if requestEnd < requestStart :
          '''start date is always the earliest'''
          '''if the query start and end are backwards, correct them'''
          temp = requestEnd
          requestEnd = requestStart
          requestStart = temp
        
      numberOfDaysToSync = (requestEnd - requestStart).days
      
      start_string = requestStart.strftime("%Y-%m-%d %H:%M:%S")
      end_string = requestEnd.strftime("%Y-%m-%d %H:%M:%S")
      logging.info(" requestStart = {0} ... requestEnd = {1} : number of days = {2}".format(start_string, end_string, numberOfDaysToSync))

      '''queue first fetch task'''
      startDate = requestEnd - datetime.timedelta(days=1)
      endDate = startDate + datetime.timedelta(days=1)
      self.fetchData (startDate, endDate, numberOfDaysToSync)

    def fetchData(self,startDate, endDate, numberOfDaysToSync):
      logging.info ( 'Queuing initial fetch')
      taskqueue.add(url='/fetch_observations', 
                    params={'numberOfDaysToSync' : numberOfDaysToSync,
                            'session_cookie' : self.session_cookie,
                            'startDate' : startDate.strftime("%Y-%m-%d %H:%M:%S"),
                            'endDate' : endDate.strftime("%Y-%m-%d %H:%M:%S") })

class SaveDataPoint(webapp2.RequestHandler):

    def post(self):
      
      sensor_owner_email = 'xtopher.brandt@gmail.com'
      
      date_time_components = re.match('(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[1-2][0-9]|3[0-1])T([0-1][0-9]|2[0-3]):([0-5][0-9]):([0-5][0-9])(?:.\d{7})?[+|-](0[0-9]|1[0-2]):(00|15|30|45)',self.request.get('date_time'))
                    
      sensor_unit_key = sensor_key(sensor_owner_key( sensor_owner_email ), sensor_serial = self.request.get('sensor_serial') )
      sensor_unit = sensor_unit_key.get()
      
      observation_data = Observation( )  
      observation_data.key = observation_key(sensor_unit_key, timestamp = self.request.get('time_stamp') )
      observation_data.date_time = datetime.datetime( int(date_time_components.group(1)),
                                      int(date_time_components.group(2)),
                                      int(date_time_components.group(3)),
                                      int(date_time_components.group(4)),
                                      int(date_time_components.group(5)),
                                      int(date_time_components.group(6)))
      observation_data.temperature_1 = float(self.request.get('temperature_1')) if self.request.get('temperature_1') <> '' else float(0)
      observation_data.temperature_2 = float(self.request.get('temperature_2')) if self.request.get('temperature_2') <> '' else float(0)
      observation_data.humidity = float(self.request.get('humidity')) if self.request.get('humidity') <> '' else float(0)
      observation_data.low_battery = float(self.request.get('low_battery')) if self.request.get('low_battery') <> '' else float(0)
      observation_data.link_quality = float(self.request.get('link_quality')) if self.request.get('link_quality') <> '' else float(0)
      observation_data.put()
          
      '''update sensor aggrigate stats'''      
      if observation_data.date_time < sensor_unit.first_observation_date :
        sensor_unit.first_observation_date = observation_data.date_time
      
      if observation_data.date_time > sensor_unit.last_observation_date :
        sensor_unit.last_observation_date = observation_data.date_time
      
      sensor_unit.observation_count = sensor_unit.observation_count + 1
      
      sensor_unit.put()

class FetchObservations(webapp2.RequestHandler):

    session_cookie =''
    
    '''
    Post Data:
      session_cookie
      numberOfDaysToSync
      startDate
      endDate
    '''
    def post(self):
      logging.info ( 'Fetch started')
      
      '''Get task input data'''
      self.session_cookie = self.request.get('session_cookie')
      numberOfDaysToSync = int(self.request.get('numberOfDaysToSync'))
      startDate = datetime.datetime.strptime(self.request.get('startDate'), '%Y-%m-%d %H:%M:%S')
      endDate = datetime.datetime.strptime(self.request.get('endDate'), '%Y-%m-%d %H:%M:%S')
      
      sensor_owner_email = 'xtopher.brandt@gmail.com'
  
      sensor = None
        
      logging.info ('  fetching from {0} to {1}'.format(startDate, endDate))
      
      fromDays = (datetime.datetime.now() - startDate).days
      toDays = (endDate - startDate).days
        
      logging.info ('   from={0} days to={1} days...'.format(fromDays, toDays))
        
      '''from is the delta from now to the earliest date'''
      '''to is the delta from the earliest date to the last date'''
      '''https://www.lacrossealerts.com/v1/observations/5394?format=json&from=-179days&to=1days'''
      download_url = 'https://www.lacrossealerts.com/v1/observations/5394?format=json&from=-{0}days&to={1}days'.format(fromDays, toDays)
      result = urlfetch.fetch(download_url,
                               method=urlfetch.GET,
                               headers={'Cookie' : self.session_cookie},
                               follow_redirects=True,
                               validate_certificate=True)
      
      logging.info( '  download response status: ' + str(result.status_code))

      sensor_data = json.loads(result.content)

      sensor_id = sensor_data['response']['id']
      sensor_serial = sensor_data['response']['serial']
      sensor = sensor_key(sensor_owner_key(sensor_owner_email), sensor_serial).get()
      
      if sensor is not None :
        logging.info ('  fetched sensor: {0}'.format(sensor.name))
      
      '''if we havn't seen this sensor before, add it'''
      if sensor is None :
        '''create a new one'''
        sensor = Sensor()
        sensor.key = sensor_key(sensor_owner_key(sensor_owner_email), sensor_serial )
        sensor.name = sensor_id
        sensor.first_observation_date = datetime.datetime.now()
        sensor.last_observation_date = datetime.datetime(1970,11,7)
        sensor.observation_count = 0
        sensor.put()

      observationCount = len(sensor_data['response']['obs'])
              
      logging.info ('  downloaded {0} data points'.format(observationCount))

      '''decrement the number of days remaining to be processed by the length of this period'''
      numberOfDaysToSync = numberOfDaysToSync - (endDate - startDate).days
        
      '''if we still have days to sync'''
      if numberOfDaysToSync > 0 :
        '''get detailed data 1 day at a time starting at the end date until we don't get anymore data'''
        if observationCount <> 0 :
          '''move the start and end dates one day back'''
          startDate = startDate - datetime.timedelta(days=1)
          endDate = startDate + datetime.timedelta(days=1)
          '''fetch another day'''
          self.fetchData (startDate, endDate, numberOfDaysToSync)
        
        '''if we're not getting detailed data and we still have days to sync, get the remaining as hourly'''
        if observationCount == 0 :
          '''set the first day in the period counting backward from the end of the last period requested'''
          startDate = endDate - datetime.timedelta(days=numberOfDaysToSync)
          '''the end date of this period is simply the end date of the last period requested'''
          endDate = endDate
          '''queue the last fetch '''
          self.fetchData(startDate, endDate, numberOfDaysToSync)
                  
      '''process the observations in this response'''
      for datapoint in sensor_data['response']['obs'] :
        observation = observation_key( sensor.key, datapoint['timeStamp']).get()

        '''if we havn't seen this observation before, add it'''
        if observation is None :
          # Add the task to the default queue.
          taskqueue.add(url='/save_datapoint', 
                        params={'sensor_serial' : sensor_serial,
                                'time_stamp' : datapoint['timeStamp'],
                                'date_time': datapoint['dateTimeISO'],
                                'temperature_1' : datapoint['values']['temp1'] if 'temp1' in datapoint['values'] else '',
                                'temperature_2' : datapoint['values']['temp2'] if 'temp2' in datapoint['values'] else '',
                                'humidity' : datapoint['values']['rh'] if 'rh' in datapoint['values'] else '',
                                'low_battery' : datapoint['values']['lowbatt'] if 'lowbatt' in datapoint['values'] else '',
                                'link_quality' : datapoint['values']['linkquality'] if 'linkquality' in datapoint['values'] else ''})

    def fetchData (self, startDate, endDate, numberOfDaysToSync) :
      logging.info ( 'Queuing recursive fetch')
      taskqueue.add(url='/fetch_observations', 
                    params={'numberOfDaysToSync' : numberOfDaysToSync,
                            'session_cookie' : self.session_cookie,
                            'startDate' : startDate,
                            'endDate' : endDate })



application = webapp2.WSGIApplication([
    ('/', MainPage),
    ('/connect', GoogleSignIn),
    ('/SignIn', SignIn),
    ('/sensor/(\d+)', Sensor_Handler),
    ('/sensor/new', New_Sensor_Handler),
    ('/sensor/(\d+)/sync', Synchronize),
    ('/save_datapoint', SaveDataPoint),
    ('/fetch_observations', FetchObservations)
], debug=True)

