import os
import webapp2
import jinja2

import datetime
import time
import random
import string
import logging
import json

from google.appengine.ext import ndb
from google.appengine.api import users
from gaesessions import get_current_session
import httplib2
from oauth2client.client import AccessTokenRefreshError
from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError

JINJA_ENVIRONMENT = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)

class SignIn(webapp2.RequestHandler):
    def get(self):

      user = users.get_current_user()
      
      self.apikey = ""

      #Try to get the apiKey from a session cookie
      session = get_current_session()
      
      # if the session is active      
      if session.is_active() and session.has_key('APIKey') :
            self.apikey = session['APIKey']
            
      # if the session is not active, create it and store the empty api key
      else :
         session.regenerate_id()
      
      # Create a state token to prevent request forgery.
      # Store it in the session for later validation.
      state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                     for x in xrange(32))
      session['state'] = state
      
      template_values = {
                         'state' : state
                        }

      template = JINJA_ENVIRONMENT.get_template('connect.html')
      self.response.write(template.render(template_values))


class GoogleSignIn(webapp2.RequestHandler) :
    def get(self):

      user = users.get_current_user()
      
      self.apikey = ""

      #Try to get the apiKey from a session cookie
      session = get_current_session()
      
      # if the session is active      
      if session.is_active() and session.has_key('APIKey') :
            self.apikey = session['APIKey']
            
      # if the session is not active, create it and store the empty api key
      else :
         session.regenerate_id()
      
      # Create a state token to prevent request forgery.
      # Store it in the session for later validation.
      state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                     for x in xrange(32))
      session['state'] = state
      
      template_values = {
                         'state' : state
                        }

      template = JINJA_ENVIRONMENT.get_template('connect.html')
      self.response.write(template.render(template_values))
      
    def post(self):

      #Try to get the apiKey from a session cookie
      session = get_current_session()
        
      # Ensure that the request is not a forgery and that the user sending
      # this connect request is the expected user.
      if self.request.get('state', '') != session['state']:
        self.response.write(json.dumps('Invalid state parmeter'))
        self.response.status = 401
        return

      code = self.request.POST

      logging.info(code)
      try:
        # Upgrade the authorization code into a credentials object
        oauth_flow = flow_from_clientsecrets('client_secrets.json', scope='', redirect_uri = 'postmessage')
        credentials = oauth_flow.step2_exchange(code)
      except FlowExchangeError:
        self.response.write(json.dumps('Failed to upgrade the authorization code.'))
        self.response.status=401
        self.response.headers['Content-Type'] = 'application/json'
        return

      # Check that the access token is valid.
      access_token = credentials.access_token
      url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token={0}'.format(access_token))

      h = httplib2.Http()
      result = json.loads(h.request(url, 'GET')[1])
      
      # If there was an error in the access token info, abort.
      if result.get('error') is not None:
        self.response.write(json.dumps('Error in access token info'))
        self.response.status=500
        return
      # Verify that the access token is valid for this app.
      if result['issued_to'] != CLIENT_ID:
        self.response.write(json.dumps('Client token does not match app'))
        self.response.status=401
        return
      stored_credentials = session.get('credentials')
      stored_gplus_id = session.get('gplus_id')
      if stored_credentials is not None and gplus_id == stored_gplus_id:
        logging.info("Current user is already connected")
        self.response.write(json.dumps('Current user is already connected.'))
        self.response.status=200

      # Store the access token in the session for later use.
      session['credentials'] = credentials
      session['gplus_id'] = gplus_id

      logging.info('Successfully connected {0}'.format())

        