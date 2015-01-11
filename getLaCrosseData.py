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

def sensor_key(sensor_owner_email, sensor_serial):
  """Constructs a Datastore key for a sensor entity with sensor_serial."""
  return ndb.Key('SensorOwner', sensor_owner_email, 'Sensor', sensor_serial)

def observation_key(sensor_owner_email, sensor_serial, timestamp):
  return ndb.Key('SensorOwner', sensor_owner_email, 'Sensor', sensor_serial, 'Observation', timestamp )

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
  low_battery = ndb.IntegerProperty()
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

      sensor_owner = SensorOwner()
      sensor_owner.key = sensor_owner_key( 'xtopher.brandt@gmail.com' )
      sensor_owner.name = 'Christopher Brandt'
      sensor_owner.put()

      numberOfDays = 10000
            
      sensorData = SensorData()
      sensorData.login()
      sensorData.sync(numberOfDays)

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
      sensor_query = Sensor.query(Sensor.name == name)
      sensors = sensor_query.fetch()
      sensor_data = []
      
      if len(sensors) > 0 :
        sensor_unit = sensors[0]
        logging.info ('Synchronizing Sensor : {0}'.format(sensor_unit.key))

      '''determine the number of days we're out of sync'''
      numberOfDays = (datetime.datetime.now() - sensor_unit.last_observation_date).days
      
      sensorData = SensorData()
      sensorData.login()
      sensorData.sync(numberOfDays)

      self.redirect('/')

class SensorData:
    
    session_cookie = ''
      
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
    def sync(self, numberOfDays):
    
      if numberOfDays > 179 :
        '''need to get all hourly data earlier than 179 days ago'''
        '''but only up to 179 days ago'''
        startDate = datetime.datetime.now() - datetime.timedelta(days=numberOfDays)
        endDate = datetime.datetime.now() - datetime.timedelta(days=179)
        self.fetchData(startDate, endDate)
        numberOfDays = 179
      
      '''get detailed data for the last 179 days 1 day at a time'''
      while numberOfDays > 0 :
        startDate = datetime.datetime.now() - datetime.timedelta(days=numberOfDays)
        endDate = startDate + datetime.timedelta(days=1)
        self.fetchData (startDate, endDate)
        numberOfDays = numberOfDays - 1
        
    def fetchData(self,startDate, endDate):
      
      logging.info ( 'Get From La Crosse Data')
      
      sensor_owner_email = 'xtopher.brandt@gmail.com'
        
      logging.info ('  fetching from {0} to {1}'.format(startDate, endDate))
      
      fromDays = (datetime.datetime.now() - startDate).days
      toDays = (endDate - startDate).days
        
      logging.info ('   from={0} days to={1} days...'.format(fromDays, toDays))
        
      '''Can only get sub-hour observations for the last 179 days and only when requestings up to 1 day at a time'''
      '''from is the delta from now to the earliest date'''
      '''to is the delta from the earliest date to the last date'''
      '''the earliest date can only be 170 days from now'''
      '''https://www.lacrossealerts.com/v1/observations/5394?format=json&from=-179days&to=1days'''
      '''beyond 179 days can only get hourly observations'''
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
      
      #logging.info( sensor_data['response']['serial'] )
              
      logging.info ('Downloaded {0} data points'.format(len(sensor_data['response']['obs'])))
      
      '''try to find the sensor unit'''
      sensor = sensor_key(sensor_owner_email = sensor_owner_email, sensor_serial = sensor_serial )      
      sensors = Sensor.query(Sensor.key == sensor).fetch()
      
      if len(sensors) == 0 :
        '''create a new one'''
        sensor_unit = Sensor()
        sensor_unit.key = sensor_key(sensor_owner_email, sensor_serial )
        sensor_unit.name = sensor_id
        sensor_unit.first_observation_date = datetime.datetime.now()
        sensor_unit.last_observation_date = datetime.datetime(1970,11,7)
        sensor_unit.observation_count = 0
        sensor_unit.put()
      else :
        '''should only be one'''
        sensor_unit = sensors[0]
        
      for datapoint in sensor_data['response']['obs'] :
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

class SaveDataPoint(webapp2.RequestHandler):

    def post(self):
      
      sensor_owner_email = 'xtopher.brandt@gmail.com'
      
      date_time_components = re.match('(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[1-2][0-9]|3[0-1])T([0-1][0-9]|2[0-3]):([0-5][0-9]):([0-5][0-9])(?:.\d{7})?[+|-](0[0-9]|1[0-2]):(00|15|30|45)',self.request.get('date_time'))
                    
      sensor = sensor_key(sensor_owner_email = sensor_owner_email, sensor_serial = self.request.get('sensor_serial') )
      sensor_unit = Sensor.query(Sensor.key == sensor).fetch()[0]

      #logging.info("Adding Observation to Sensor: {0}".format(sensor_unit.name))
      
      observation_data = Observation( )  
      observation_data.key = observation_key(sensor_owner_email = sensor_owner_email, sensor_serial = self.request.get('sensor_serial'), timestamp = self.request.get('time_stamp') )
      observation_data.date_time = datetime.datetime( int(date_time_components.group(1)),
                                      int(date_time_components.group(2)),
                                      int(date_time_components.group(3)),
                                      int(date_time_components.group(4)),
                                      int(date_time_components.group(5)),
                                      int(date_time_components.group(6)))
      observation_data.temperature_1 = float(self.request.get('temperature_1')) if self.request.get('temperature_1') <> '' else float(0)
      observation_data.temperature_2 = float(self.request.get('temperature_2')) if self.request.get('temperature_2') <> '' else float(0)
      observation_data.humidity = float(self.request.get('humidity')) if self.request.get('humidity') <> '' else float(0)
      observation_data.low_battery = int(self.request.get('low_battery')) if self.request.get('low_battery') <> '' else float(0)
      observation_data.link_quality = float(self.request.get('link_quality')) if self.request.get('link_quality') <> '' else float(0)
      observation_data.put()
          
      '''update sensor aggrigate stats'''      
      if observation_data.date_time < sensor_unit.first_observation_date :
        sensor_unit.first_observation_date = observation_data.date_time
      
      if observation_data.date_time > sensor_unit.last_observation_date :
        sensor_unit.last_observation_date = observation_data.date_time
      
      sensor_unit.observation_count = sensor_unit.observation_count + 1
      
      sensor_unit.put()
      
      #logging.info ('Datapoint {0} saved to {1}'.format(observation_data.key,observation_data.key.parent()))
    
application = webapp2.WSGIApplication([
    ('/', MainPage),
    ('/connect', GoogleSignIn),
    ('/SignIn', SignIn),
    ('/sensor/(\d+)', Sensor_Handler),
    ('/sensor/new', New_Sensor_Handler),
    ('/sensor/(\d+)/sync', Synchronize),
    ('/save_datapoint', SaveDataPoint)
], debug=True)

