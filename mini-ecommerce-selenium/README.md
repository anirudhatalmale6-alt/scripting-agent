# Mini E-Commerce – Selenium Test Project

Automated UI tests for the **Users** page of the Mini E-Commerce application  
(`http://localhost:8000`), written in **Java 11 + Selenium 4 + TestNG**.

---

## Prerequisites

| Tool | Version | Download |
|------|---------|----------|
| Java JDK | 11 or later | https://adoptium.net |
| Maven | 3.8+ | https://maven.apache.org/download.cgi |
| Google Chrome | Latest | https://www.google.com/chrome |
| Mini E-Commerce app | running on port 8000 | (your local server) |

> **ChromeDriver** is managed automatically by **WebDriverManager** — no manual download needed.

---

## Project Structure

```
mini-ecommerce-selenium/
├── pom.xml                                          # Maven dependencies
└── src/
    └── test/
        ├── java/com/ecommerce/
        │   ├── pages/
        │   │   └── UsersPage.java                   # Page Object Model
        │   └── tests/
        │       ├── BaseTest.java                    # WebDriver setup/teardown
        │       └── UserPageTest.java                # All test cases
        └── resources/
            └── testng.xml                           # TestNG suite config
```

---

## How to Run

### Step 1 – Make sure the app is running
```bash
# The Mini E-Commerce app must be accessible at:
http://localhost:8000
```

### Step 2 – Clone / download this project
```bash
cd mini-ecommerce-selenium
```

### Step 3 – Run the tests with Maven
```bash
mvn clean test
```

Maven will:
1. Download all dependencies (first run only)
2. Auto-download the matching ChromeDriver binary
3. Open Chrome, execute all tests, then close Chrome
4. Print a summary to the console

---

## Running Headless (no browser window)

Open `src/test/java/com/ecommerce/tests/BaseTest.java` and uncomment line:

```java
// options.addArguments("--headless=new");
```

Then re-run `mvn clean test`.

---

## Test Cases

| # | Test Method | What it validates |
|---|-------------|-------------------|
| 1 | `testApplicationLoadsAtCorrectUrl` | URL contains `localhost:8000` |
| 2 | `testUsersNavButtonIsVisible` | **Users** nav button visible |
| 3 | `testProductsNavButtonIsVisible` | **Products** nav button visible |
| 4 | `testOrdersNavButtonIsVisible` | **Orders** nav button visible |
| 5 | `testUsersPageHeadingIsCorrect` | Section heading text = "Users" |
| 6 | `testNameInputIsVisibleAndEnabled` | Name field visible & enabled |
| 7 | `testNameInputPlaceholder` | Name field placeholder = "Name" |
| 8 | `testEmailInputIsVisibleAndEnabled` | Email field visible & enabled |
| 9 | `testEmailInputPlaceholder` | Email field placeholder = "Email" |
| 10 | `testAddUserButtonIsVisible` | Add User button visible & enabled |
| 11 | `testAddUserButtonText` | Button label = "Add User" |
| 12 | `testAddUserButtonClickTriggersValidation` | Empty-form click stays on page |
| 13 | `testTableIdHeaderIsVisible` | Table "ID" column header visible |
| 14 | `testTableNameHeaderIsVisible` | Table "Name" column header visible |
| 15 | `testTableEmailHeaderIsVisible` | Table "Email" column header visible |

---

## Viewing Test Reports

After `mvn clean test`, open the Surefire HTML report:

```
target/surefire-reports/index.html
```

Open it in any browser for a full pass/fail breakdown.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Connection refused` | Make sure the app is running on `http://localhost:8000` |
| `ChromeDriver version mismatch` | WebDriverManager auto-resolves this; update Chrome if very old |
| `ElementNotInteractableException` | XPath selectors may need updating if the app's HTML changes |
| Tests run but all fail | Verify element `placeholder` / button text matches your app version |
