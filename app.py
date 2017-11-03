import sys
import os
import uuid
import urllib.parse
import base64
import itertools

import todoist
import requests
from flask import Flask
from flask import request
from flask import json
from flask import render_template
from flask import redirect
from flask import session
from flask import abort
from flask import url_for
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'sqlite:////tmp/test.db'
)
app.secret_key = os.environ['APP_SECRET_KEY']
db = SQLAlchemy(app)
client_id = os.environ['CLIENT_ID']
client_secret = os.environ['CLIENT_SECRET']


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    oauth_token = db.Column(db.String(64), nullable=True)


class LocationLabel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'),
        nullable=False)
    user= db.relationship(
        'User',
        backref=db.backref('location_labels', lazy='dynamic')
    )
    label_id = db.Column(db.Integer, nullable=False, index=True)
    name = db.Column(db.String, nullable=False)
    long = db.Column(db.Float, nullable=False)
    lat = db.Column(db.Float, nullable=False)
    loc_trigger = db.Column(db.String, nullable=False)
    radius = db.Column(db.Float, nullable=False)


@app.route('/')
def index():
    user_id = session.get('user_id')
    kwargs = {}
    if user_id is not None:
        user = User.query.get(user_id)
        labels = requests.get(
            'https://beta.todoist.com/API/v8/labels',
            params=dict(token=user.oauth_token)
        ).json()
        kwargs['labels'] = labels
        api = todoist.TodoistAPI(user.oauth_token)
        kwargs['user_full_name'] = api.user.get('full_name')
        # map from label id to location labels
        location_labels = {}
        for label_id, group in itertools.groupby(
            user.location_labels.all(),
            lambda ll: ll.label_id
        ):
            location_labels[label_id] = list(group)
        kwargs['location_labels'] = location_labels
    return render_template('index.html', **kwargs)


@app.route('/authorize')
def authorize():
    state = base64.b64encode(os.urandom(32)).decode('utf8')
    session['oauth_secret_state'] = state
    return redirect(
        'https://todoist.com/oauth/authorize?' + urllib.parse.urlencode(dict(
            client_id=client_id,
            scope='data:read_write',
            state=state,
        ))
    )


@app.route('/oauth/redirect')
def oauth_redirect():
    state = session['oauth_secret_state']
    if request.args.get('state') != state:
        return abort(401)
    code = request.args.get('code')
    if not code:
        return abort(400)
    resp = requests.post('https://todoist.com/oauth/access_token', data=dict(
        client_id=client_id,
        client_secret=client_secret,
        code=code,
        redirect_uri=url_for('authorize', _external=True),
    ))
    resp.raise_for_status()
    access_token = resp.json()['access_token']
    api = todoist.TodoistAPI(access_token)
    user_id = api.user.get_id()
    user = User.query.get(user_id)
    if user is None:
        user = User(id=user_id, oauth_token=access_token)
    else:
        user.oauth_token = access_token
    db.session.commit()
    session['user_id'] = user.id
    return redirect(url_for('index'))


@app.route('/webhook', methods=['POST'])
def webhook():
    event = request.json
    if event['event_name'] not in ['item:added', 'item:updated']:
        return ''
    initiator = event['initiator']
    event_data = event['event_data']
    user = User.query.get(initiator['id'])
    api = todoist.TodoistAPI(user.oauth_token)
    for label_id in event_data['labels']:
        loc_labels = user.location_labels.filter_by(label_id=label_id).all()
        if not loc_labels:
            continue
        for loc_label in loc_labels:
            temp_id = uuid.uuid4().hex
            req_uuid = uuid.uuid4().hex
            api.reminders.add(
                event_data['id'],
                type='location',
                name=loc_label.name,
                loc_lat=loc_label.lat,
                loc_long=loc_label.long,
                loc_trigger=loc_label.loc_trigger,
                radius=loc_label.radius 
            )
    api.commit()
    return 'ok'

if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == 'initdb':
        db.create_all()
    else:
        app.run(debug=True, use_reloader=True)