package com.ecommerce.pages;

import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;

import java.time.Duration;

/**
 * Page Object Model for the Users page of Mini E-Commerce application.
 * Encapsulates all locators and actions for the Users section.
 */
public class UsersPage {

    private final WebDriver driver;
    private final WebDriverWait wait;

    // ── Navigation locators ──────────────────────────────────────────────────
    private final By usersNavBtn    = By.xpath("//button[normalize-space()='Users']");
    private final By productsNavBtn = By.xpath("//button[normalize-space()='Products']");
    private final By ordersNavBtn   = By.xpath("//button[normalize-space()='Orders']");

    // ── Page header ──────────────────────────────────────────────────────────
    private final By pageHeading = By.xpath("//h2[normalize-space()='Users']");

    // ── Add-User form ────────────────────────────────────────────────────────
    private final By nameInput    = By.xpath("//input[@placeholder='Name']");
    private final By emailInput   = By.xpath("//input[@placeholder='Email']");
    private final By addUserBtn   = By.xpath("//button[normalize-space()='Add User']");

    // ── Users table ──────────────────────────────────────────────────────────
    private final By tableIdHeader    = By.xpath("//th[normalize-space()='ID']");
    private final By tableNameHeader  = By.xpath("//th[normalize-space()='Name']");
    private final By tableEmailHeader = By.xpath("//th[normalize-space()='Email']");

    // ────────────────────────────────────────────────────────────────────────

    public UsersPage(WebDriver driver) {
        this.driver = driver;
        this.wait   = new WebDriverWait(driver, Duration.ofSeconds(10));
    }

    // ── Navigation helpers ───────────────────────────────────────────────────

    /** Returns the text of the browser's current page title. */
    public String getPageTitle() {
        return driver.getTitle();
    }

    /** Clicks the Users nav button. */
    public void clickUsersNav() {
        wait.until(ExpectedConditions.elementToBeClickable(usersNavBtn)).click();
    }

    /** Returns the Users nav button element (for visibility assertions). */
    public WebElement getUsersNavButton() {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(usersNavBtn));
    }

    /** Returns the Products nav button element. */
    public WebElement getProductsNavButton() {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(productsNavBtn));
    }

    /** Returns the Orders nav button element. */
    public WebElement getOrdersNavButton() {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(ordersNavBtn));
    }

    // ── Page heading ─────────────────────────────────────────────────────────

    /** Returns the "Users" section heading element. */
    public WebElement getUsersHeading() {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(pageHeading));
    }

    /** Returns the text of the "Users" section heading. */
    public String getUsersHeadingText() {
        return getUsersHeading().getText();
    }

    // ── Form elements ─────────────────────────────────────────────────────────

    /** Returns the Name input field. */
    public WebElement getNameInput() {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(nameInput));
    }

    /** Returns the Email input field. */
    public WebElement getEmailInput() {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(emailInput));
    }

    /** Returns the Add User button. */
    public WebElement getAddUserButton() {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(addUserBtn));
    }

    /** Returns the placeholder text of the Name field. */
    public String getNamePlaceholder() {
        return getNameInput().getAttribute("placeholder");
    }

    /** Returns the placeholder text of the Email field. */
    public String getEmailPlaceholder() {
        return getEmailInput().getAttribute("placeholder");
    }

    /** Returns the visible label text of the Add User button. */
    public String getAddUserButtonText() {
        return getAddUserButton().getText();
    }

    /** Clicks the Add User button. */
    public void clickAddUser() {
        wait.until(ExpectedConditions.elementToBeClickable(addUserBtn)).click();
    }

    // ── Table column headers ──────────────────────────────────────────────────

    /** Returns the ID column header element. */
    public WebElement getTableIdHeader() {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(tableIdHeader));
    }

    /** Returns the Name column header element. */
    public WebElement getTableNameHeader() {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(tableNameHeader));
    }

    /** Returns the Email column header element. */
    public WebElement getTableEmailHeader() {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(tableEmailHeader));
    }

    // ── State queries ─────────────────────────────────────────────────────────

    /** Returns true if the Name input is displayed and enabled. */
    public boolean isNameInputReady() {
        WebElement el = getNameInput();
        return el.isDisplayed() && el.isEnabled();
    }

    /** Returns true if the Email input is displayed and enabled. */
    public boolean isEmailInputReady() {
        WebElement el = getEmailInput();
        return el.isDisplayed() && el.isEnabled();
    }

    /** Returns true if the Add User button is displayed and enabled. */
    public boolean isAddUserButtonReady() {
        WebElement el = getAddUserButton();
        return el.isDisplayed() && el.isEnabled();
    }
}
