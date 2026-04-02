import requests
import sys

try:
    print("Fetching customers...")
    r = requests.get('http://localhost:5000/api/customers')
    print("Status:", r.status_code)
    customers = r.json()
    print("Customers:", customers)

    print("Creating temp customer for delete smoke test...")
    payload = {
        'name': 'Temp Smoke Customer',
        'address': 'Test Address',
        'gstin': '',
        'state_code': '24',
        'phone': '',
        'email': '',
    }
    create = requests.post('http://localhost:5000/api/customers', json=payload)
    print("Create status:", create.status_code)
    create.raise_for_status()
    created = create.json()
    customer_id = created.get('id')

    print(f"Deleting temp customer ID {customer_id}...")
    r = requests.delete(f'http://localhost:5000/api/customers/{customer_id}')
    print("Status:", r.status_code)
    if r.status_code != 200:
        print("Response text:", r.text)
    else:
        print("Response json:", r.json())
except Exception as e:
    print("Error:", e)
