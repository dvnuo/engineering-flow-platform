---
name: mobilex-test-cases-generator
description: "Generate mobile automation test cases from Jira tickets. Creates Cucumber feature files and Java step implementations for iOS/Android. Use when: user provides Jira ticket(s) and wants automated test cases."
version: 1.0.0
owner: qa-platform
triggers:
  - "generate mobile tests"
  - "mobilex test cases"
  - "jira to mobile automation"
  - "mobile test from jira"
  - "/mobilex-test"
tools:
  - jira_get_issue_by_url
  - jira_search
  - github_create_or_update_file
  - github_get_file_content
  - git_clone
  - git_commit
  - git_push
  - run_command
strategy:
  - "1. Parse Jira ticket(s) - supports single or multiple (e.g., EFP-123 or EFP-123,EFP-456)"
  - "2. Call jira_get_issue_by_url to fetch each ticket's details"
  - "3. After getting result, respond with [FINISH] + summary of what was fetched"
  - "4. Include in [FINISH]: summary, description (key points), acceptance criteria"
  - "5. Wait for user confirmation before generating test code"
  - "6. After confirmation, generate test scenarios (scenario outline + examples)"
  - "7. Generate Cucumber feature file + Step Definitions + Java implementations"
  - "8. Commit code to GitHub"
output_format: markdown
---

# MobileX Test Cases Generator

Generate mobile automation test cases from Jira tickets.

## Overview

1. **Jira Parsing** - Fetch requirement details from Jira (summary, description, AC, comments)
2. **Scenario Generation** - Generate test scenarios covering requirements (Gherkin/Cucumber)
3. **Code Generation** - Create complete test code:
   - Cucumber Feature files
   - Java Step Definitions
   - Java DeviceStepDriver Interface
   - Common Implementation
   - iOS Implementation
   - Android Implementation
4. **Git Commit** - Commit code to GitHub

## Output File Structure

```
<project-root>/
├── src/test/resources/features/
│   └── {ticket}-{feature-name}.feature          # Feature file
├── src/test/java/{package}/steps/
│   └── {FeatureName}Steps.java                  # Step Definitions
├── src/test/java/{package}/driver/
│   └── DeviceStepDriver.java                    # Interface
├── src/test/java/{package}/driver/impl/
│   ├── common/
│   │   └── DeviceStepDriverImpl.java           # Common impl
│   ├── ios/
│   │   └── IOSDeviceStepDriver.java            # iOS impl
│   └── android/
│       └── AndroidDeviceStepDriver.java        # Android impl
```

## Generated File Details

### 1. Feature File (.feature)
Describe test scenarios using Cucumber/Gherkin syntax:

```gherkin
@EFP-123
Feature: User Login
  As a mobile user
  I want to login with email and password
  So that I can access my account

  Background:
    Given the app is launched
    And the user is on the login screen

  @smoke
  Scenario: Successful login with valid credentials
    When the user enters "test@example.com" in the email field
    And the user enters "Password123" in the password field
    And the user taps the login button
    Then the user should see the home screen
    And the welcome message should be displayed
```

### 2. Step Definitions (Java)
Map Gherkin steps to code implementation:

```java
@Given("the app is launched")
public void theAppIsLaunched() {
    driver.launchApp();
}
```

### 3. DeviceStepDriver Interface
Define standard interface for device operations:

```java
public interface DeviceStepDriver {
    void launchApp();
    void tap(String locator);
    void enterText(String locator, String text);
    String getText(String locator);
}
```

### 4. Implementation Classes
- **Common** - Generic operations for all platforms
- **iOS** - Appium iOS-specific implementation
- **Android** - Appium Android-specific implementation

## Usage Examples

- "generate mobile tests for EFP-123"
- "mobilex test cases: EFP-123, EFP-456"
- "jira to mobile automation EFP-123"
- "/mobilex-test EFP-123"

## User Interaction Flow

1. **Provide Ticket** → Skill parses and fetches Jira info
2. **Confirm Info** → Display summary + AC, user confirms/supplements
3. **Generate Scenarios** → Create feature draft
4. **Review Scenarios** → User confirms or modifies scenarios
5. **Generate Code** → One-click generate complete test code
6. **Submit Code** → Git push + PR link

## Notes

- Supports multi-ticket batch processing
- Auto-detects Git repo and clones to workspace
- Avoids overwriting existing files (checks SHA)
- Generated code follows project's existing naming conventions