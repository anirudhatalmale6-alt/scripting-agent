import os
import pytest
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

@pytest.fixture
def driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(options=options)
    yield driver
    driver.quit()

def test_delete_user_id(driver):
    base_url = os.getenv("SFCC_SITE_URL", "https://localhost:8000")
    user_id = "12345"  # Example user ID
    driver.delete(f"{base_url}/{user_id}")  # Updated to use DELETE method

    # Wait for the page to load and check for a specific element or title
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "h1")))
    
    # Assert the title or a specific element to verify the response
    assert "User Deleted" in driver.title  # Adjust based on expected title
    assert driver.find_element(By.TAG_NAME, "h1").text == "User Deleted"  # Adjust based on expected content