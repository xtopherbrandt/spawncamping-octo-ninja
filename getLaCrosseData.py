import cgi
import os
import urllib
import logging
import time
import json
import re

from google.appengine.api import users
from google.appengine.ext import ndb
from google.appengine.api import urlfetch
from datetime import datetime, date
from google.appengine.api import taskqueue

import jinja2
import webapp2

JINJA_ENVIRONMENT = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)

# the write rate should be limited to ~1/second.

def sensor_key(sensor_serial):
  """Constructs a Datastore key for a sensor entity with sensor_serial."""
  return ndb.Key('Sensor', sensor_serial)

def observation_key( sensor_serial, timestamp):
  return ndb.Key( 'Sensor', sensor_serial, 'Observation', timestamp )

class Sensor(ndb.Model):
  '''Models an individual sensor'''
  name = ndb.StringProperty()
  
class Observation(ndb.Model):
    """Models an individual observation data point entry."""
    '''The observation key is the timestamp'''
    date_time = ndb.DateTimeProperty()
    temperature_1 = ndb.FloatProperty()
    temperature_2 = ndb.FloatProperty()
    humidity = ndb.IntegerProperty()
    low_battery = ndb.IntegerProperty()
    link_quality = ndb.IntegerProperty()

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

    
class Synchronize(webapp2.RequestHandler):

    def get(self):

      self.sync()

      self.redirect('/')

          
    def sync(self):
      
      logging.info ( 'Get From La Crosse Data')
      
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

      session_cookie = ''
      logged_in = False
      last_redirect = '';
      
      if result.headers['set-cookie']:
        for cookie_parts in result.header_msg.getheaders('set-cookie') :
            session_cookie = cookie_parts.split(';')[0]
      
      while result.status_code == 302 :
              logging.info ( 'redirect : ' + result.headers['location']  )
              last_redirect = result.headers['location']
              result = urlfetch.fetch(base_url + result.headers['location'],
                                      method=urlfetch.POST,
                                      headers={'Cookie' : session_cookie},
                                      follow_redirects=False)

      if last_redirect == '/devices' :
         logged_in = True
         logging.info( 'Logged In' ) 
         
          
      logging.info ('Synchronizing...')
      
      download_url = 'https://www.lacrossealerts.com/v1/observations/5394?format=json&from=-1days'
      result = urlfetch.fetch(download_url,
                               method=urlfetch.GET,
                               headers={'Cookie' : session_cookie},
                               follow_redirects=True,
                               validate_certificate=True)
      
               
      logging.info( 'Download Response Status: ' + str(result.status_code))

      sensor_data = json.loads(result.content)

      sensor_id = sensor_data['response']['id']
      sensor_serial = sensor_data['response']['serial']
      
      logging.info( sensor_data['response']['serial'] )
              
      logging.info ('Downloaded {0} data points'.format(len(sensor_data['response']['obs'])))
      
      sensor_unit = Sensor()
      sensor_unit.key = sensor_key( sensor_serial )
      sensor_unit.name = sensor_id
      sensor_unit.put()
      
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
      
      date_time_components = re.match('(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[1-2][0-9]|3[0-1])T([0-1][0-9]|2[0-3]):([0-5][0-9]):([0-5][0-9])(?:.\d{7})?[+|-](0[0-9]|1[0-2]):(00|15|30|45)',self.request.get('date_time'))
      
      observation = Observation( )  
      observation.key = observation_key( sensor_serial = self.request.get('sensor_serial'), timestamp = self.request.get('time_stamp') )
      observation.date_time = datetime( int(date_time_components.group(1)),
                                      int(date_time_components.group(2)),
                                      int(date_time_components.group(3)),
                                      int(date_time_components.group(4)),
                                      int(date_time_components.group(5)),
                                      int(date_time_components.group(6)))
      observation.temperature_1 = float(self.request.get('temperature_1')) if self.request.get('temperature_1') <> '' else float(0)
      observation.temperature_2 = float(self.request.get('temperature_2')) if self.request.get('temperature_2') <> '' else float(0)
      observation.humidity = int(self.request.get('humidity')) if self.request.get('humidity') <> '' else float(0)
      observation.low_battery = int(self.request.get('low_battery')) if self.request.get('low_battery') <> '' else float(0)
      observation.link_quality = int(self.request.get('link_quality')) if self.request.get('link_quality') <> '' else float(0)
      observation.put()
          
      logging.info ('Datapoint {0} saved to {1}'.format(observation.key,observation.key.parent()))
    
application = webapp2.WSGIApplication([
    ('/', MainPage),
    ('/sensor/(\d+)', Sensor_Handler),
    ('/sync', Synchronize),
    ('/save_datapoint', SaveDataPoint)
], debug=True)

