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
def setup_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    yield driver
    driver.quit()

def test_api_products(setup_driver):
    base_url = os.getenv("SFCC_SITE_URL", "https://localhost:8000")
    endpoint = f"{base_url}/api/products"
    
    # Sample product data
    product_data = {
        "name": "Test Product",
        "description": "This is a test product.",
        "price": 19.99,
        "category": "Test Category",
        "stock": 100
    }
    
    # Make a POST request to add a new product
    response = requests.post(endpoint, json=product_data)
    
    # Assert the response status code
    assert response.status_code == 201, f"Expected status code 201, got {response.status_code}"
    
    # Verify the product was added by checking the response data
    response_data = response.json()
    assert response_data['name'] == product_data['name'], "Product name does not match"
    assert response_data['description'] == product_data['description'], "Product description does not match"
    
    # Optionally, verify the product appears in the product list
    driver = setup_driver
    driver.get(f"{base_url}/products")
    
    # Wait for the product to be visible on the page
    WebDriverWait(driver, 10).until(
        EC.visibility_of_element_located((By.XPATH, f"//h2[text()='{product_data['name']}']"))
    )
    
    # Assert that the product is displayed on the page
    assert driver.find_element(By.XPATH, f"//h2[text()='{product_data['name']}']"), "Product not found on the page"