import os
import pytest
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

@pytest.fixture
def browser():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    yield driver
    driver.quit()

def test_post_endpoint(browser):
    base_url = os.getenv("SFCC_SITE_URL", "https://localhost:8000")
    browser.get(base_url)

    # Perform the POST request using JavaScript
    browser.execute_script("""
        var xhr = new XMLHttpRequest();
        xhr.open("POST", "/", true);
        xhr.setRequestHeader("Content-Type", "application/json;charset=UTF-8");
        xhr.onload = function() {
            if (xhr.status === 200) {
                document.body.innerHTML = xhr.responseText;
            }
        };
        xhr.send(JSON.stringify({ /* your payload here */ }));
    """)

    # Wait for the title to be present and assert it
    WebDriverWait(browser, 10).until(EC.title_is("Expected Title"))  # Replace with the expected title
    assert "Expected Title" in browser.title  # Replace with the expected title

    # Alternatively, you can assert for a specific element
    WebDriverWait(browser, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "h1")))  # Replace with a valid selector
    element = browser.find_element(By.CSS_SELECTOR, "h1")  # Replace with a valid selector
    assert element.is_displayed()  # Check if the element is displayed
    assert element.text == "Expected Header"  # Replace with the expected text of the element