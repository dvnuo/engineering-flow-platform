#!/usr/bin/env python3
"""
MobileX Test Cases Generator
Generates Cucumber feature files and Java step implementations from Jira tickets.
"""

import re
import sys
from typing import Dict, List, Optional, Tuple

# ============== FEATURE FILE GENERATOR ==============

def generate_feature_file(ticket: str, summary: str, description: str, 
                          acceptance_criteria: List[str], scenarios: List[Dict]) -> str:
    """Generate a Cucumber feature file from Jira information."""
    
    feature_name = to_feature_name(summary)
    
    lines = [
        f"@{ticket}",
        f"Feature: {feature_name}",
        f"  {summary}",
        "",
        "  Background:",
        "    Given the app is launched",
        "    And the user is logged in",
        "",
    ]
    
    for i, scenario in enumerate(scenarios, 1):
        scenario_name = scenario.get('name', f'Scenario {i}')
        tags = f" @{ticket}-{i}"
        if scenario.get('smoke'):
            tags += " @smoke"
        
        lines.append(f"  {tags}")
        lines.append(f"  Scenario: {scenario_name}")
        
        for step in scenario.get('given', []):
            lines.append(f"    Given {step}")
        for step in scenario.get('when', []):
            lines.append(f"    When {step}")
        for step in scenario.get('then', []):
            lines.append(f"    Then {step}")
        
        lines.append("")
    
    return "\n".join(lines)

def to_feature_name(text: str) -> str:
    """Convert text to a feature name (Title Case)."""
    # Remove common prefixes
    text = re.sub(r'^(Feature:|Story:|Task:|Bug:)\s*', '', text, flags=re.IGNORECASE)
    # Convert to title case
    words = text.replace('-', ' ').replace('_', ' ').split()
    return ' '.join(word.capitalize() for word in words)

# ============== STEP DEFINITIONS GENERATOR ==============

def generate_step_definitions(package: str, feature_name: str, ticket: str,
                              scenarios: List[Dict]) -> str:
    """Generate Java step definitions file."""
    
    class_name = f"{to_pascal_case(feature_name)}Steps"
    
    lines = [
        "package " + package + ".steps;",
        "",
        "import io.cucumber.java.en.*;",
        "import org.junit.Assert;",
        "import " + package + ".driver.DeviceStepDriver;",
        "",
        f"/**",
        f" * Step definitions for {feature_name}",
        f" * Generated from: {ticket}",
        f" */",
        f"public class {class_name} {{",
        "",
        f"    private final DeviceStepDriver driver;",
        "",
        f"    public {class_name}(DeviceStepDriver driver) {{",
        f"        this.driver = driver;",
        f"    }}",
        "",
    ]
    
    # Generate Given steps
    given_steps = set()
    for scenario in scenarios:
        for step in scenario.get('given', []):
            given_steps.add(step)
    
    for step in given_steps:
        method_name = step_to_method_name(step, 'Given')
        lines.append(f"    @Given(\"{escape_cucumber(step)}\")")
        lines.append(f"    public void {method_name}() {{")
        lines.append(f"        // TODO: Implement")
        lines.append(f"    }}")
        lines.append("")
    
    # Generate When steps
    when_steps = set()
    for scenario in scenarios:
        for step in scenario.get('when', []):
            when_steps.add(step)
    
    for step in when_steps:
        method_name = step_to_method_name(step, 'When')
        lines.append(f"    @When(\"{escape_cucumber(step)}\")")
        lines.append(f"    public void {method_name}() {{")
        lines.append(f"        // TODO: Implement")
        lines.append(f"    }}")
        lines.append("")
    
    # Generate Then steps
    then_steps = set()
    for scenario in scenarios:
        for step in scenario.get('then', []):
            then_steps.add(step)
    
    for step in then_steps:
        method_name = step_to_method_name(step, 'Then')
        lines.append(f"    @Then(\"{escape_cucumber(step)}\")")
        lines.append(f"    public void {method_name}() {{")
        lines.append(f"        // TODO: Implement")
        lines.append(f"    }}")
        lines.append("")
    
    lines.append("}")
    
    return "\n".join(lines)

def step_to_method_name(step: str, prefix: str) -> str:
    """Convert a Gherkin step to a Java method name."""
    # Remove the prefix pattern
    step = re.sub(r'^(the user |I |the system )', '', step.lower())
    step = re.sub(r'["<>]', '', step)
    step = re.sub(r'\s+', '_', step.strip())
    step = re.sub(r'[^a-z0-9_]', '', step)
    return prefix.lower() + "_" + step[:50]

def escape_cucumber(text: str) -> str:
    """Escape special characters for Cucumber step definitions."""
    return text.replace('{', '{{').replace('}', '}}')

# ============== JAVA FILE GENERATORS ==============

def generate_device_step_driver_interface(package: str) -> str:
    """Generate the DeviceStepDriver interface."""
    
    lines = [
        "package " + package + ".driver;",
        "",
        "/**",
        " * Interface for device automation step driver.",
        " * Provides standard methods for mobile app interactions.",
        " */",
        "public interface DeviceStepDriver {",
        "",
        "    // App lifecycle",
        "    void launchApp();",
        "    void closeApp();",
        "    void restartApp();",
        "",
        "    // Navigation",
        "    void navigateToScreen(String screen);",
        "    void goBack();",
        "",
        "    // Interactions",
        "    void tap(String locator);",
        "    void tapButton(String buttonName);",
        "    void tapCoordinate(int x, int y);",
        "    void longPress(String locator, int durationMs);",
        "",
        "    // Input",
        "    void enterText(String locator, String text);",
        "    void clearText(String locator);",
        "    void selectDropdown(String locator, String value);",
        "",
        "    // Assertions",
        "    String getText(String locator);",
        "    boolean isElementDisplayed(String locator);",
        "    boolean isScreenDisplayed(String screen);",
        "    String getErrorMessage();",
        "",
        "    // Platform info",
        "    String getPlatform();",
        "    String getPlatformVersion();",
        "}",
    ]
    
    return "\n".join(lines)

def generate_common_driver_impl(package: str, feature_name: str) -> str:
    """Generate the common driver implementation."""
    
    class_name = f"CommonDeviceStepDriver"
    
    lines = [
        "package " + package + ".driver.impl.common;",
        "",
        "import " + package + ".driver.DeviceStepDriver;",
        "",
        f"/**",
        f" * Common implementation of DeviceStepDriver.",
        f" * Shared logic across iOS and Android platforms.",
        f" * Generated from: {feature_name}",
        f" */",
        f"public abstract class {class_name} implements DeviceStepDriver {{",
        "",
        "    protected String platform;",
        "",
        "    @Override",
        "    public void launchApp() {",
        "        String appPackage = getAppPackage();",
        "        String appActivity = getAppActivity();",
        "        executeLaunch(appPackage, appActivity);",
        "    }",
        "",
        "    @Override",
        "    public void closeApp() {",
        "        executeClose();",
        "    }",
        "",
        "    @Override",
        "    public void navigateToScreen(String screen) {",
        "        // Screen navigation mapping - implement in subclasses",
        "        throw new UnsupportedOperationException(\"Screen not supported: \" + screen);",
        "    }",
        "",
        "    @Override",
        "    public void tap(String locator) {",
        "        findElement(locator).click();",
        "    }",
        "",
        "    @Override",
        "    public void enterText(String locator, String text) {",
        "        WebElement element = findElement(locator);",
        "        element.clear();",
        "        element.sendKeys(text);",
        "    }",
        "",
        "    @Override",
        "    public String getText(String locator) {",
        "        return findElement(locator).getText();",
        "    }",
        "",
        "    @Override",
        "    public boolean isElementDisplayed(String locator) {",
        "        try {",
        "            return findElement(locator).isDisplayed();",
        "        } catch (Exception e) {",
        "            return false;",
        "        }",
        "    }",
        "",
        "    @Override",
        "    public String getPlatform() {",
        "        return platform;",
        "    }",
        "",
        "    // Abstract methods to be implemented by platform-specific classes",
        "    protected abstract String getAppPackage();",
        "    protected abstract String getAppActivity();",
        "    protected abstract org.openqa.selenium.WebElement findElement(String locator);",
        "    protected abstract void executeLaunch(String package_, String activity);",
        "    protected abstract void executeClose();",
        "}",
    ]
    
    return "\n".join(lines)

def generate_ios_driver(package: str, feature_name: str, bundle_id: str) -> str:
    """Generate the iOS driver implementation."""
    
    class_name = f"IOSDeviceStepDriver"
    
    lines = [
        "package " + package + ".driver.impl.ios;",
        "",
        "import " + package + ".driver.DeviceStepDriver;",
        "import io.appium.java_client.AppiumDriver;",
        "import io.appium.java_client.ios.IOSDriver;",
        "import org.openqa.selenium.WebElement;",
        "",
        f"/**",
        f" * iOS-specific implementation of DeviceStepDriver.",
        f" * Uses Appium iOS Driver.",
        f" * Generated from: {feature_name}",
        f" */",
        f"public class {class_name} extends " + package + ".driver.impl.common.CommonDeviceStepDriver {{",
        "",
        "    private final AppiumDriver<WebElement> driver;",
        "",
        f"    public {class_name}(AppiumDriver<WebElement> driver) {{",
        "        this.platform = \"iOS\";",
        "        this.driver = driver;",
        "    }}",
        "",
        "    @Override",
        "    protected String getAppPackage() {",
        f'        return "{bundle_id}";',
        "    }",
        "",
        "    @Override",
        "    protected String getAppActivity() {",
        "        return null; // iOS doesn't have activities",
        "    }",
        "",
        "    @Override",
        "    protected WebElement findElement(String locator) {",
        "        if (locator.startsWith(\"accessibility:\")) {",
        "            return driver.findElementByAccessibilityId(locator.replace(\"accessibility:\", \"\"));",
        "        } else if (locator.startsWith(\"xpath:\")) {",
        "            return driver.findElementByXPath(locator.replace(\"xpath:\", \"\"));",
        "        }",
        "        return driver.findElementByAccessibilityId(locator);",
        "    }",
        "",
        "    @Override",
        "    protected void executeLaunch(String package_, String activity) {",
        "        driver.launchApp();",
        "    }",
        "",
        "    @Override",
        "    protected void executeClose() {",
        "        driver.closeApp();",
        "    }",
        "",
        "    @Override",
        "    public String getPlatformVersion() {",
        "        return driver.getCapabilities().getCapability(\"platformVersion\").toString();",
        "    }",
        "}",
    ]
    
    return "\n".join(lines)

def generate_android_driver(package: str, feature_name: str, app_package: str, main_activity: str) -> str:
    """Generate the Android driver implementation."""
    
    class_name = f"AndroidDeviceStepDriver"
    
    lines = [
        "package " + package + ".driver.impl.android;",
        "",
        "import " + package + ".driver.DeviceStepDriver;",
        "import io.appium.java_client.AppiumDriver;",
        "import io.appium.java_client.android.AndroidDriver;",
        "import org.openqa.selenium.WebElement;",
        "",
        f"/**",
        f" * Android-specific implementation of DeviceStepDriver.",
        f" * Uses Appium Android Driver.",
        f" * Generated from: {feature_name}",
        f" */",
        f"public class {class_name} extends " + package + ".driver.impl.common.CommonDeviceStepDriver {{",
        "",
        "    private final AppiumDriver<WebElement> driver;",
        "",
        f"    public {class_name}(AppiumDriver<WebElement> driver) {{",
        "        this.platform = \"Android\";",
        "        this.driver = driver;",
        "    }}",
        "",
        "    @Override",
        "    protected String getAppPackage() {",
        f'        return "{app_package}";',
        "    }",
        "",
        "    @Override",
        "    protected String getAppActivity() {",
        f'        return "{main_activity}";',
        "    }",
        "",
        "    @Override",
        "    protected WebElement findElement(String locator) {",
        "        if (locator.startsWith(\"id:\")) {",
        "            return driver.findElementById(locator.replace(\"id:\", \"\"));",
        "        } else if (locator.startsWith(\"xpath:\")) {",
        "            return driver.findElementByXPath(locator.replace(\"xpath:\", \"\"));",
        "        } else if (locator.startsWith(\"text:\")) {",
        "            String text = locator.replace(\"text:\", \"\");",
        "            return driver.findElementByXPath(\"//*[@text='\" + text + \"']\");",
        "        }",
        "        return driver.findElementById(locator);",
        "    }",
        "",
        "    @Override",
        "    protected void executeLaunch(String package_, String activity) {",
        "        ((AndroidDriver) driver).startActivity(package_, activity);",
        "    }",
        "",
        "    @Override",
        "    protected void executeClose() {",
        "        driver.closeApp();",
        "    }",
        "",
        "    @Override",
        "    public String getPlatformVersion() {",
        "        return ((AndroidDriver) driver).getPlatformVersion();",
        "    }",
        "}",
    ]
    
    return "\n".join(lines)

# ============== UTILITY FUNCTIONS ==============

def to_pascal_case(text: str) -> str:
    """Convert text to PascalCase."""
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
    words = text.replace('-', ' ').replace('_', ' ').split()
    return ''.join(word.capitalize() for word in words if word)

def generate_package_name(group_id: str, module: str) -> str:
    """Generate Java package name from groupId and module."""
    parts = group_id.split('.') + [module]
    return '.'.join(parts)

def parse_jira_tickets(ticket_input: str) -> List[str]:
    """Parse Jira ticket input (comma or space separated)."""
    tickets = re.split(r'[,;\s]+', ticket_input.strip())
    return [t.strip().upper() for t in tickets if t.strip()]

def extract_acceptance_criteria(description: str) -> List[str]:
    """Extract acceptance criteria from Jira description."""
    criteria = []
    
    # Look for numbered lists
    patterns = [
        r'(?:^|\n)\s*(?:\d+[\.\)]\s*)(.*?)(?=\n(?:\d+\s|$))',
        r'(?:^|\n)\s*[-*]\s*(.*?)(?=\n(?:[-*]\s|$))',
        r'(?:^|\n)(?:AC|Acceptance Criteria)[:\s]*(.*?)(?=\n\n|$)',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, description, re.MULTILINE | re.IGNORECASE)
        criteria.extend(matches)
    
    # If no structured criteria found, use full description
    if not criteria:
        criteria = [description] if description else []
    
    return criteria[:10]  # Limit to 10 criteria

# ============== MAIN ==============

def main():
    """Main entry point for CLI usage."""
    if len(sys.argv) < 2:
        print("Usage: python generate_code.py <command> [options]")
        print("Commands:")
        print("  feature <ticket> <summary> - Generate feature file")
        print("  steps <package> <feature> - Generate step definitions")
        print("  interface <package> - Generate DeviceStepDriver interface")
        print("  ios <package> <bundle-id> - Generate iOS driver")
        print("  android <package> <app-package> <activity> - Generate Android driver")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == 'feature' and len(sys.argv) >= 4:
        ticket = sys.argv[2]
        summary = sys.argv[3]
        print(generate_feature_file(ticket, summary, "", [], []))
    elif command == 'interface' and len(sys.argv) >= 3:
        print(generate_device_step_driver_interface(sys.argv[2]))
    else:
        print(f"Unknown command or missing arguments: {command}")
        sys.exit(1)

if __name__ == '__main__':
    main()