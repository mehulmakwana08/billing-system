import requests
import sys

try:
    print("Fetching customers...")
    r = requests.get('http://localhost:5000/api/customers')
    print("Status:", r.status_code)
    customers = r.json()
    print("Customers:", customers)

    print("Deleting customer ID 2...")
    r = requests.delete('http://localhost:5000/api/customers/2')
    print("Status:", r.status_code)
    if r.status_code != 200:
        print("Response text:", r.text)
    else:
        print("Response json:", r.json())
except Exception as e:
    print("Error:", e)
