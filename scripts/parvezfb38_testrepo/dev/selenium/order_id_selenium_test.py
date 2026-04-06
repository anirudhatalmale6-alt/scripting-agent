import os
import pytest
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

@pytest.fixture
def setup_browser():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")  # Run headless Chrome
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    yield driver
    driver.quit()

def test_delete_order(setup_browser):
    driver = setup_browser
    base_url = os.getenv("SFCC_SITE_URL", "https://localhost:8000")
    order_id = "12345"  # Example order ID
    url = f"{base_url}/{order_id}"

    # Send DELETE request to the modified endpoint
    driver.get(url)
    driver.execute_script("fetch(arguments[0], { method: 'DELETE' });", url)

    # Wait for the response and check for a specific element or message indicating deletion
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "h1")))  # Adjust selector as needed
        assert "Order Deleted" in driver.page_source  # Adjust message check as needed
    except Exception as e:
        pytest.fail(f"Test failed due to: {str(e)}")