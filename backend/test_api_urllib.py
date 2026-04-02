import urllib.request
import urllib.error
import json

try:
    payload = json.dumps({
        'name': 'Temp Urllib Smoke Customer',
        'address': 'Test Address',
        'gstin': '',
        'state_code': '24',
        'phone': '',
        'email': '',
    }).encode('utf-8')

    create_req = urllib.request.Request(
        'http://localhost:5000/api/customers',
        data=payload,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(create_req) as create_resp:
        created = json.loads(create_resp.read().decode('utf-8'))

    cid = created.get('id')
    req = urllib.request.Request(f'http://localhost:5000/api/customers/{cid}', method='DELETE')
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        print("Body:", response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print("HTTP Error:", e.code)
    print("Body:", e.read().decode('utf-8'))
except Exception as e:
    print("Error:", e)
