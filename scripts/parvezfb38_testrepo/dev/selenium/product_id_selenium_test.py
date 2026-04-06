import os
import pytest
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

@pytest.fixture(scope="module")
def setup():
    # Set up headless Chrome
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    yield driver
    driver.quit()

def test_delete_product(setup):
    driver = setup
    base_url = os.getenv("SFCC_SITE_URL", "https://localhost:8000")
    product_id = "12345"  # Example product ID
    url = f"{base_url}/{product_id}"

    # Navigate to the product delete page
    driver.get(url)

    # Wait for the title to contain the product ID
    WebDriverWait(driver, 10).until(EC.title_contains(product_id))

    # Assert that the title is correct
    assert product_id in driver.title

    # Simulate deleting the product
    delete_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".delete-product-button")))
    delete_button.click()

    # Wait for a confirmation message or updated element
    confirmation_message = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".delete-confirmation")))
    assert confirmation_message is not None
    assert "Product deleted successfully" in confirmation_message.text