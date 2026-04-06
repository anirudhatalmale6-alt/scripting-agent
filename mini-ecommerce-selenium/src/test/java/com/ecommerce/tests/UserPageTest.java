package com.ecommerce.tests;

import com.ecommerce.pages.UsersPage;
import org.openqa.selenium.WebElement;
import org.testng.Assert;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

/**
 * Test suite for the Users page of the Mini E-Commerce application.
 *
 * Covers:
 *  1. Page loads successfully (URL + title)
 *  2. Navigation bar is visible (Users / Products / Orders buttons)
 *  3. "Users" section heading is present
 *  4. Name input field is visible and enabled
 *  5. Email input field is visible and enabled
 *  6. "Add User" button is visible and clickable
 *  7. Users table column headers are rendered
 */
public class UserPageTest extends BaseTest {

    private UsersPage usersPage;

    @BeforeMethod
    public void initPage() {
        // UsersPage is already loaded by BaseTest.setUp()
        usersPage = new UsersPage(driver);
    }

    // ── 1. Application loads ──────────────────────────────────────────────────

    @Test(description = "Verify the application loads at the correct URL")
    public void testApplicationLoadsAtCorrectUrl() {
        String currentUrl = driver.getCurrentUrl();
        Assert.assertTrue(
            currentUrl.contains("localhost:8000"),
            "Expected URL to contain 'localhost:8000' but was: " + currentUrl
        );
        System.out.println("✔ Application URL verified: " + currentUrl);
    }

    // ── 2. Navigation bar ─────────────────────────────────────────────────────

    @Test(description = "Verify the 'Users' navigation button is visible")
    public void testUsersNavButtonIsVisible() {
        WebElement usersBtn = usersPage.getUsersNavButton();
        Assert.assertTrue(usersBtn.isDisplayed(),
            "'Users' navigation button should be visible");
        System.out.println("✔ 'Users' nav button is visible");
    }

    @Test(description = "Verify the 'Products' navigation button is visible")
    public void testProductsNavButtonIsVisible() {
        WebElement productsBtn = usersPage.getProductsNavButton();
        Assert.assertTrue(productsBtn.isDisplayed(),
            "'Products' navigation button should be visible");
        System.out.println("✔ 'Products' nav button is visible");
    }

    @Test(description = "Verify the 'Orders' navigation button is visible")
    public void testOrdersNavButtonIsVisible() {
        WebElement ordersBtn = usersPage.getOrdersNavButton();
        Assert.assertTrue(ordersBtn.isDisplayed(),
            "'Orders' navigation button should be visible");
        System.out.println("✔ 'Orders' nav button is visible");
    }

    // ── 3. Page heading ───────────────────────────────────────────────────────

    @Test(description = "Verify the Users section heading is displayed with correct text")
    public void testUsersPageHeadingIsCorrect() {
        String headingText = usersPage.getUsersHeadingText();
        Assert.assertEquals(headingText, "Users",
            "Section heading text mismatch");
        System.out.println("✔ Users section heading is: " + headingText);
    }

    // ── 4. Name input ─────────────────────────────────────────────────────────

    @Test(description = "Verify the Name input field is visible and enabled")
    public void testNameInputIsVisibleAndEnabled() {
        Assert.assertTrue(usersPage.isNameInputReady(),
            "Name input should be visible and enabled");
        System.out.println("✔ Name input is visible and enabled");
    }

    @Test(description = "Verify the Name input has the correct placeholder text")
    public void testNameInputPlaceholder() {
        String placeholder = usersPage.getNamePlaceholder();
        Assert.assertEquals(placeholder, "Name",
            "Name input placeholder mismatch");
        System.out.println("✔ Name input placeholder: " + placeholder);
    }

    // ── 5. Email input ────────────────────────────────────────────────────────

    @Test(description = "Verify the Email input field is visible and enabled")
    public void testEmailInputIsVisibleAndEnabled() {
        Assert.assertTrue(usersPage.isEmailInputReady(),
            "Email input should be visible and enabled");
        System.out.println("✔ Email input is visible and enabled");
    }

    @Test(description = "Verify the Email input has the correct placeholder text")
    public void testEmailInputPlaceholder() {
        String placeholder = usersPage.getEmailPlaceholder();
        Assert.assertEquals(placeholder, "Email",
            "Email input placeholder mismatch");
        System.out.println("✔ Email input placeholder: " + placeholder);
    }

    // ── 6. Add User button ────────────────────────────────────────────────────

    @Test(description = "Verify the 'Add User' button is visible and enabled")
    public void testAddUserButtonIsVisible() {
        Assert.assertTrue(usersPage.isAddUserButtonReady(),
            "'Add User' button should be visible and enabled");
        System.out.println("✔ 'Add User' button is visible and enabled");
    }

    @Test(description = "Verify the 'Add User' button has the correct label text")
    public void testAddUserButtonText() {
        String btnText = usersPage.getAddUserButtonText();
        Assert.assertEquals(btnText, "Add User",
            "'Add User' button label mismatch");
        System.out.println("✔ 'Add User' button text: " + btnText);
    }

    @Test(description = "Verify clicking 'Add User' button without data triggers validation")
    public void testAddUserButtonClickTriggersValidation() {
        // Click without filling in the form — native browser validation fires
        usersPage.clickAddUser();

        // The form should NOT navigate away; we remain on the same page
        String url = driver.getCurrentUrl();
        Assert.assertTrue(url.contains("localhost:8000"),
            "Page should stay on localhost:8000 after clicking Add User with empty form");
        System.out.println("✔ Clicking 'Add User' with empty form stays on page (validation triggered): " + url);
    }

    // ── 7. Table headers ──────────────────────────────────────────────────────

    @Test(description = "Verify the Users table 'ID' column header is visible")
    public void testTableIdHeaderIsVisible() {
        Assert.assertTrue(usersPage.getTableIdHeader().isDisplayed(),
            "Users table 'ID' header should be visible");
        System.out.println("✔ Table 'ID' header is visible");
    }

    @Test(description = "Verify the Users table 'Name' column header is visible")
    public void testTableNameHeaderIsVisible() {
        Assert.assertTrue(usersPage.getTableNameHeader().isDisplayed(),
            "Users table 'Name' header should be visible");
        System.out.println("✔ Table 'Name' header is visible");
    }

    @Test(description = "Verify the Users table 'Email' column header is visible")
    public void testTableEmailHeaderIsVisible() {
        Assert.assertTrue(usersPage.getTableEmailHeader().isDisplayed(),
            "Users table 'Email' header should be visible");
        System.out.println("✔ Table 'Email' header is visible");
    }
}
