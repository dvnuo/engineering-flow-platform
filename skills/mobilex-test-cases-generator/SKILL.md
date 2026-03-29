---
name: mobilex-test-cases-generator
description: Generate mobile automation test cases from Jira tickets. Creates Cucumber feature files and Java step implementations for iOS/Android. Use when: user provides Jira ticket(s) and wants automated test cases.
version: 1.0.0
owner: qa-platform
triggers:
  - "generate mobile tests"
  - "mobilex test cases"
  - "jira to mobile automation"
  - "mobile test from jira"
  - "/mobilex-test"
tools:
  - jira_get_issue
  - jira_search
  - github_create_or_update_file
  - github_get_file_content
  - git_clone
  - git_commit
  - git_push
  - run_command
strategy:
  - "1. 解析 Jira ticket(s) - 支持单个或多个 (如: EFP-123 或 EFP-123,EFP-456)"
  - "2. 调用 jira_get_issue 获取每个 ticket 的详细信息"
  - "3. 提取: summary, description, acceptance criteria, comments"
  - "4. 如信息不足则 [ASK_USER] 补充；信息完整则展示给用户确认"
  - "5. 生成测试场景 (scenario outline + examples)"
  - "6. 生成 Cucumber feature 文件"
  - "7. 用户确认场景后生成: Step Definitions + Java Interface + 实现类"
  - "8. 代码提交到 GitHub"
output_format: markdown
---

# MobileX Test Cases Generator

通过 Jira ticket 生成移动端自动化测试用例。

## 功能概述

1. **Jira 解析** - 从 Jira 获取需求详情 (summary, description, AC, comments)
2. **场景生成** - 生成覆盖需求的测试场景 (Gherkin/Cucumber)
3. **代码生成** - 创建完整的测试代码:
   - Cucumber Feature 文件
   - Java Step Definitions
   - Java DeviceStepDriver Interface
   - Common Implementation
   - iOS Implementation
   - Android Implementation
4. **Git 提交** - 代码提交到 GitHub

## 输出文件结构

```
<project-root>/
├── src/test/resources/features/
│   └── {ticket}-{feature-name}.feature          # Feature 文件
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

## 生成文件说明

### 1. Feature 文件 (.feature)
使用 Cucumber/Gherkin 语法描述测试场景:

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
将 Gherkin 步骤映射到代码实现:

```java
@Given("the app is launched")
public void theAppIsLaunched() {
    driver.launchApp();
}
```

### 3. DeviceStepDriver Interface
定义设备操作的标准接口:

```java
public interface DeviceStepDriver {
    void launchApp();
    void tap(String locator);
    void enterText(String locator, String text);
    String getText(String locator);
}
```

### 4. 实现类
- **Common** - 所有平台的通用操作
- **iOS** - Appium iOS 特定实现
- **Android** - Appium Android 特定实现

## 示例用法

- "generate mobile tests for EFP-123"
- "mobilex test cases: EFP-123, EFP-456"
- "jira to mobile automation EFP-123"
- "/mobilex-test EFP-123"

## 用户交互流程

1. **提供 Ticket** → Skill 解析并获取 Jira 信息
2. **确认信息** → 展示 summary + AC，用户确认/补充
3. **生成场景** → 生成 feature 草案
4. **审核场景** → 用户确认或修改场景
5. **生成代码** → 一键生成完整测试代码
6. **提交代码** → Git push + PR 链接

## 注意事项

- 支持多 ticket 批量处理
- 自动检测 Git 仓库并克隆到 workspace
- 避免覆盖已有文件 (检查 SHA)
- 生成的代码遵循项目现有的命名规范