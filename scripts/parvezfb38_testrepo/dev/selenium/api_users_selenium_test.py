import os
import pytest
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

@pytest.fixture
def setup_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    yield driver
    driver.quit()

def test_api_users(setup_driver):
    base_url = os.getenv("SFCC_SITE_URL", "https://localhost:8000")
    endpoint = f"{base_url}/api/users"
    
    # Sample user data
    user_data = {
        "username": "testuser",
        "password": "securepassword",
        "email": "testuser@example.com"
    }

    # Sending POST request to add a new user
    response = requests.post(endpoint, json=user_data)

    # Assert the response status code
    assert response.status_code == 201, f"Expected status code 201, got {response.status_code}"

    # Verify the user was added by checking the response data
    response_data = response.json()
    assert response_data['username'] == user_data['username'], "Username does not match"
    assert response_data['email'] == user_data['email'], "Email does not match"

    # Optionally, you can use Selenium to verify the user in the UI
    setup_driver.get(base_url)
    WebDriverWait(setup_driver, 10).until(EC.title_contains("User List"))

    # Check if the new user appears in the user list (assuming there's a user list page)
    user_list = setup_driver.find_element(By.ID, "user-list")
    assert user_data['username'] in user_list.text, "New user not found in user list"