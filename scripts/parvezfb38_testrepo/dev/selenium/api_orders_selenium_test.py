import os
import pytest
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

@pytest.fixture(scope="module")
def setup_browser():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    yield driver
    driver.quit()

def test_api_orders(setup_browser):
    base_url = os.getenv("SFCC_SITE_URL", "https://localhost:8000")
    endpoint = f"{base_url}/api/orders"
    
    # Sample order data
    order_data = {
        "customer_id": "12345",
        "items": [
            {
                "product_id": "abc123",
                "quantity": 2
            },
            {
                "product_id": "xyz789",
                "quantity": 1
            }
        ],
        "shipping_address": {
            "street": "123 Main St",
            "city": "Anytown",
            "state": "CA",
            "zip": "12345"
        },
        "payment_info": {
            "card_number": "4111111111111111",
            "expiration_date": "12/25",
            "cvv": "123"
        }
    }

    # Sending POST request to create a new order
    response = requests.post(endpoint, json=order_data, verify=False)
    
    # Assert the response status code
    assert response.status_code == 201, f"Expected status code 201 but got {response.status_code}"
    
    # Optionally, check the response content
    response_data = response.json()
    assert "order_id" in response_data, "Order ID not found in response"
    
    # Using Selenium to verify the order creation on the frontend
    setup_browser.get(base_url)
    WebDriverWait(setup_browser, 10).until(EC.title_contains("Order Confirmation"))
    
    # Assert that the title contains "Order Confirmation"
    assert "Order Confirmation" in setup_browser.title, "Title does not contain 'Order Confirmation'"