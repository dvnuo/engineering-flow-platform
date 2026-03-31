# Code Templates

## 1. Cucumber Feature Template

```gherkin
@{ticket}
Feature: {Feature Name}
  {Description}

  Background:
    Given the app is launched
    And the user is logged in
    And the user is on the {screen} screen

  @{ticket}-1
  Scenario: {Scenario Name}
    When the user performs {action}
    Then the system should {expected result}

  @{ticket}-2 @smoke
  Scenario Outline: {Parameterized Scenario}
    When the user enters "<input>" in the <field> field
    Then the system should display "<expected>"
    Examples:
      | input | field | expected |
      | test@example.com | email | valid |
      | invalid | email | error message |
```

## 2. Step Definitions Template

```java
package {package}.steps;

import io.cucumber.java.en.*;
import org.junit.Assert;

/**
 * Step definitions for {Feature Name}
 * Generated from: {ticket}
 */
public class {FeatureName}Steps {
    
    private final DeviceStepDriver driver;
    
    public {FeatureName}Steps(DeviceStepDriver driver) {
        this.driver = driver;
    }
    
    // Given steps
    @Given("the app is launched")
    public void theAppIsLaunched() {{
        driver.launchApp();
    }}
    
    @Given("the user is on the {string} screen")
    public void theUserIsOnTheScreen(String screen) {{
        driver.navigateToScreen(screen);
    }}
    
    // When steps
    @When("the user taps the {string} button")
    public void theUserTapsTheButton(String button) {{
        driver.tapButton(button);
    }}
    
    @When("the user enters {string} in the {string} field")
    public void theUserEntersInTheField(String text, String field) {{
        driver.enterText(field, text);
    }}
    
    // Then steps
    @Then("the user should see the {string} screen")
    public void theUserShouldSeeTheScreen(String screen) {{
        Assert.assertTrue("Expected screen not found", driver.isScreenDisplayed(screen));
    }}
    
    @Then("the error message {string} should be displayed")
    public void theErrorMessageShouldBeDisplayed(String message) {{
        Assert.assertEquals(message, driver.getErrorMessage());
    }}
}
```

## 3. DeviceStepDriver Interface Template

```java
package {package}.driver;

/**
 * Interface for device automation step driver.
 * Provides standard methods for mobile app interactions.
 * Generated from: {ticket}
 */
public interface DeviceStepDriver {
    
    // App lifecycle
    void launchApp();
    void closeApp();
    void restartApp();
    
    // Navigation
    void navigateToScreen(String screen);
    void goBack();
    
    // Interactions
    void tap(String locator);
    void tapButton(String buttonName);
    void tapCoordinate(int x, int y);
    void longPress(String locator, int durationMs);
    
    // Input
    void enterText(String locator, String text);
    void clearText(String locator);
    void selectDropdown(String locator, String value);
    
    // Assertions
    String getText(String locator);
    boolean isElementDisplayed(String locator);
    boolean isScreenDisplayed(String screen);
    String getErrorMessage();
    
    // Platform info
    String getPlatform();
    String getPlatformVersion();
}
```

## 4. Common Implementation Template

```java
package {package}.driver.impl.common;

import {package}.driver.DeviceStepDriver;

/**
 * Common implementation of DeviceStepDriver.
 * Shared logic across iOS and Android platforms.
 * Generated from: {ticket}
 */
public abstract class CommonDeviceStepDriver implements DeviceStepDriver {
    
    protected String platform;
    
    @Override
    public void launchApp() {{
        String appPackage = getAppPackage();
        String appActivity = getAppActivity();
        // Common launch logic
        executeLaunch(appPackage, appActivity);
    }}
    
    @Override
    public void closeApp() {{
        executeClose();
    }}
    
    @Override
    public void navigateToScreen(String screen) {{
        // Screen navigation mapping
        switch (screen.toLowerCase()) {{
            case "login":
                navigateToLoginScreen();
                break;
            case "home":
                navigateToHomeScreen();
                break;
            // Add more screens as needed
            default:
                throw new UnsupportedOperationException("Screen not supported: " + screen);
        }}
    }}
    
    @Override
    public void tap(String locator) {{
        findElement(locator).click();
    }}
    
    @Override
    public void enterText(String locator, String text) {{
        WebElement element = findElement(locator);
        element.clear();
        element.sendKeys(text);
    }}
    
    @Override
    public String getText(String locator) {{
        return findElement(locator).getText();
    }}
    
    @Override
    public boolean isElementDisplayed(String locator) {{
        try {{
            return findElement(locator).isDisplayed();
        }} catch (Exception e) {{
            return false;
        }}
    }}
    
    @Override
    public String getPlatform() {{
        return platform;
    }}
    
    // Abstract methods to be implemented by platform-specific classes
    protected abstract String getAppPackage();
    protected abstract String getAppActivity();
    protected abstract WebElement findElement(String locator);
    protected abstract void executeLaunch(String package_, String activity);
    protected abstract void executeClose();
    protected abstract void navigateToLoginScreen();
    protected abstract void navigateToHomeScreen();
}
```

## 5. iOS Implementation Template

```java
package {package}.driver.impl.ios;

import {package}.driver.DeviceStepDriver;
import io.appium.java_client.AppiumDriver;
import io.appium.java_client.ios.IOSDriver;
import org.openqa.selenium.WebElement;

/**
 * iOS-specific implementation of DeviceStepDriver.
 * Uses Appium iOS Driver.
 * Generated from: {ticket}
 */
public class IOSDeviceStepDriver extends CommonDeviceStepDriver {
    
    private final AppiumDriver<WebElement> driver;
    
    public IOSDeviceStepDriver(AppiumDriver<WebElement> driver) {{
        this.platform = "iOS";
        this.driver = driver;
    }}
    
    @Override
    protected String getAppPackage() {{
        return "{ios-app-bundle-id}";
    }}
    
    @Override
    protected String getAppActivity() {{
        // iOS doesn't have activities, return null
        return null;
    }}
    
    @Override
    protected WebElement findElement(String locator) {{
        // iOS-specific element finding
        if (locator.startsWith("accessibility:")) {{
            return driver.findElementByAccessibilityId(locator.replace("accessibility:", ""));
        }} else if (locator.startsWith("xpath:")) {{
            return driver.findElementByXPath(locator.replace("xpath:", ""));
        }}
        return driver.findElementByAccessibilityId(locator);
    }}
    
    @Override
    protected void executeLaunch(String package_, String activity) {{
        // iOS launch implementation
        driver.launchApp();
    }}
    
    @Override
    protected void executeClose() {{
        driver.closeApp();
    }}
    
    @Override
    protected void navigateToLoginScreen() {{
        // iOS specific navigation
        driver.findElementByAccessibilityId("LoginTab").click();
    }}
    
    @Override
    protected void navigateToHomeScreen() {{
        // iOS specific navigation
        driver.findElementByAccessibilityId("HomeTab").click();
    }}
    
    @Override
    public String getPlatformVersion() {{
        return driver.getCapabilities().getCapability("platformVersion").toString();
    }}
    
    @Override
    public void tapButton(String buttonName) {{
        tap("accessibility:" + buttonName);
    }}
    
    @Override
    public void tapCoordinate(int x, int y) {{
        // iOS tap by coordinates
        driver.tap(new TapOptions().withCoordinates(x, y));
    }}
    
    @Override
    public void longPress(String locator, int durationMs) {{
        WebElement element = findElement(locator);
        driver.longPress(element, durationMs);
    }}
    
    @Override
    public void selectDropdown(String locator, String value) {{
        tap(locator);
        driver.findElementByAccessibilityId(value).click();
    }}
    
    @Override
    public String getErrorMessage() {{
        try {{
            return getText("accessibility:ErrorMessage");
        }} catch (Exception e) {{
            return null;
        }}
    }}
}
```

## 6. Android Implementation Template

```java
package {package}.driver.impl.android;

import {package}.driver.DeviceStepDriver;
import io.appium.java_client.AppiumDriver;
import io.appium.java_client.android.AndroidDriver;
import org.openqa.selenium.WebElement;

/**
 * Android-specific implementation of DeviceStepDriver.
 * Uses Appium Android Driver.
 * Generated from: {ticket}
 */
public class AndroidDeviceStepDriver extends CommonDeviceStepDriver {
    
    private final AppiumDriver<WebElement> driver;
    
    public AndroidDeviceStepDriver(AppiumDriver<WebElement> driver) {{
        this.platform = "Android";
        this.driver = driver;
    }}
    
    @Override
    protected String getAppPackage() {{
        return "{android-app-package}";
    }}
    
    @Override
    protected String getAppActivity() {{
        return "{android-main-activity}";
    }}
    
    @Override
    protected WebElement findElement(String locator) {{
        // Android-specific element finding
        if (locator.startsWith("id:")) {{
            return driver.findElementById(locator.replace("id:", ""));
        }} else if (locator.startsWith("xpath:")) {{
            return driver.findElementByXPath(locator.replace("xpath:", ""));
        }} else if (locator.startsWith("text:")) {{
            return driver.findElementByXPath("//*[@text='" + locator.replace("text:", "") + "']");
        }}
        return driver.findElementById(locator);
    }}
    
    @Override
    protected void executeLaunch(String package_, String activity) {{
        // Android launch implementation
        ((AndroidDriver) driver).startActivity(package_, activity);
    }}
    
    @Override
    protected void executeClose() {{
        driver.closeApp();
    }}
    
    @Override
    protected void navigateToLoginScreen() {{
        // Android specific navigation
        driver.findElementById("com.example.app:id/login_tab").click();
    }}
    
    @Override
    protected void navigateToHomeScreen() {{
        // Android specific navigation
        driver.findElementById("com.example.app:id/home_tab").click();
    }}
    
    @Override
    public String getPlatformVersion() {{
        return ((AndroidDriver) driver).getPlatformVersion();
    }}
    
    @Override
    public void tapButton(String buttonName) {{
        tap("id:com.example.app:id/" + buttonName.toLowerCase() + "_button");
    }}
    
    @Override
    public void tapCoordinate(int x, int y) {{
        ((AndroidDriver) driver).tap(x, y);
    }}
    
    @Override
    public void longPress(String locator, int durationMs) {{
        WebElement element = findElement(locator);
        ((AndroidDriver) driver).longPress(element, durationMs);
    }}
    
    @Override
    public void selectDropdown(String locator, String value) {{
        tap(locator);
        // Wait for dropdown to open and select
        waitForElement("id:android:id/text1");
        driver.findElementByXPath("//android.widget.TextView[@text='" + value + "']").click();
    }}
    
    @Override
    public String getErrorMessage() {{
        try {{
            return getText("id:com.example.app:id/error_message");
        }} catch (Exception e) {{
            return null;
        }}
    }}
}
```

## 7. Cucumber Runner Template

```java
package {package};

import io.cucumber.junit.Cucumber;
import io.cucumber.junit.CucumberOptions;
import org.junit.runner.RunWith;

@RunWith(Cucumber.class)
@CucumberOptions(
    plugin = {{"pretty", "html:target/cucumber-report.html"}},
    features = {"src/test/resources/features"},
    glue = {{"{package}.steps"}},
    tags = {{"@smoke"}},
    dryRun = false
)
public class CucumberRunner {{
    // Runner class for executing Cucumber tests
}}
```

## Notes

1. **{package}** - Replace with actual package name, e.g., `com.example.mobile.login`
2. **{ticket}** - Replace with Jira ticket ID, e.g., `EFP-123`
3. **{ios-app-bundle-id}** - Replace with iOS App Bundle ID
4. **{android-app-package}** - Replace with Android App Package
5. **{android-main-activity}** - Replace with Android Main Activity