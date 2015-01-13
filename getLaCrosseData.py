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
    
'''Synchronize a sensor Handler'''
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
      sensorData.sync()

      self.redirect('/')

class SensorData:
    
    session_cookie = ''
    senseor = None
    
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
    def sync(self):

      last_observation = datetime.datetime(1970,11,7,0,0,0)

      if self.sensor is not None :
        last_observation = self.sensor.last_observation_date
        
      numberOfDaysToSync = (datetime.datetime.now() - last_observation).days
        
      observationCount = -1
      fromDays = 0
      
      '''get detailed data 1 day at a time starting today until we don't get anymore data'''
      while observationCount <> 0 and numberOfDaysToSync > 0 :
        fromDays = fromDays + 1
        numberOfDaysToSync = numberOfDaysToSync - 1
        startDate = datetime.datetime.now() - datetime.timedelta(days=fromDays)
        endDate = startDate + datetime.timedelta(days=1)
        observationCount = self.fetchData (startDate, endDate)
        
      '''if we still have days to sync, get the remaining as hourly'''
      if numberOfDaysToSync > 0 :
        startDate = last_observation
        endDate = datetime.datetime.now() - datetime.timedelta(days=fromDays)
        self.fetchData(startDate, endDate)
              
    def fetchData(self,startDate, endDate):
      
      logging.info ( 'Get From La Crosse Data')
      
      sensor_owner_email = 'xtopher.brandt@gmail.com'
        
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
      
      logging.info( 'Download Response Status: ' + str(result.status_code))

      sensor_data = json.loads(result.content)

      sensor_id = sensor_data['response']['id']
      sensor_serial = sensor_data['response']['serial']
              
      logging.info ('Downloaded {0} data points'.format(len(sensor_data['response']['obs'])))
      
      if self.sensor is None :
        '''create a new one'''
        self.sensor = Sensor()
        self.sensor.key = sensor_key(sensor_owner_key(sensor_owner_email), sensor_serial )
        self.sensor.name = sensor_id
        self.sensor.first_observation_date = datetime.datetime.now()
        self.sensor.last_observation_date = datetime.datetime(1970,11,7)
        self.sensor.observation_count = 0
        self.sensor.put()
            
      for datapoint in sensor_data['response']['obs'] :
        observation = observation_key( self.sensor.key, datapoint['timeStamp']).get()
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

      '''return the number of observations we received so that the calling procedure can determine what to request next'''
      return len(sensor_data['response']['obs'])
    
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
    
application = webapp2.WSGIApplication([
    ('/', MainPage),
    ('/connect', GoogleSignIn),
    ('/SignIn', SignIn),
    ('/sensor/(\d+)', Sensor_Handler),
    ('/sensor/new', New_Sensor_Handler),
    ('/sensor/(\d+)/sync', Synchronize),
    ('/save_datapoint', SaveDataPoint)
], debug=True)

