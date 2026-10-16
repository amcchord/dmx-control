#!/usr/bin/env python3
"""Independent public HTTP/TLS probe. Exit 1 for failure or <14-day cert life."""
import json
import socket
import ssl
import sys
from datetime import datetime, timezone
from urllib.request import urlopen

host = 'dmx.50day.io'
try:
    with socket.create_connection((host, 443), timeout=10) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as tls:
            expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(tls.getpeercert()['notAfter']),timezone.utc)
    days = (expires-datetime.now(timezone.utc)).total_seconds()/86400
    with urlopen(f'https://{host}/api/health',timeout=10) as response:
        health=json.load(response)
    with urlopen(f'https://{host}/',timeout=10) as response:
        assert response.status==200,'Homepage unavailable'
    assert health.get('ok'), 'Backend unhealthy'
    assert days>=14,f'Certificate expires in {days:.1f} days'
    print(json.dumps({'ok':True,'certificate_days_remaining':round(days,1),'certificate_expires':expires.isoformat(),'output_errors':health.get('output_errors',0)}))
except Exception as error:
    print(json.dumps({'ok':False,'error':str(error)}));sys.exit(1)
