import os
import pytest
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

def test_saucedemo_homepage(setup_browser):
    driver = setup_browser
    driver.get(os.getenv("SFCC_SITE_URL") + "/")
    
    # Wait for the page to load and check the title
    WebDriverWait(driver, 10).until(EC.title_contains("Swag Labs"))
    assert "Swag Labs" in driver.title

def test_webtours_homepage(setup_browser):
    driver = setup_browser
    driver.get(os.getenv("SFCC_SITE_URL") + "/")
    
    # Wait for the page to load and check for a specific element
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "username")))
    assert driver.find_element(By.NAME, "username").is_displayed()