# 文件命名规范

## 命名原则

1. **驼峰命名** - Java 文件使用 CamelCase
2. **Kebab-case** - Feature 文件使用连字符
3. **语义明确** - 文件名需表达功能内容

## 文件命名规则

### Feature 文件
```
{ticket}-{feature-name}.feature
```
- ticket: Jira ticket ID (如: EFP-123)
- feature-name: 功能名称 (kebab-case)

**示例**:
- `EFP-123-user-login.feature`
- `EFP-456-product-search.feature`

### Java 文件

#### Step Definitions
```
{FeatureName}Steps.java
```
**示例**:
- `UserLoginSteps.java`
- `ProductSearchSteps.java`

#### Interface
```
DeviceStepDriver.java
```

#### Implementation
```
{Platform}{FeatureName}Driver.java
```
**示例**:
- `CommonDeviceStepDriver.java`
- `IOSDeviceStepDriver.java`
- `AndroidDeviceStepDriver.java`

## 包名规范

```
{groupId}.mobile.{module}.steps
{groupId}.mobile.{module}.driver
{groupId}.mobile.{module}.driver.impl.common
{groupId}.mobile.{module}.driver.impl.ios
{groupId}.mobile.{module}.driver.impl.android
```

**示例** (groupId=com.example, module=login):
```
com.example.mobile.login.steps.UserLoginSteps
com.example.mobile.login.driver.DeviceStepDriver
com.example.mobile.login.driver.impl.common.CommonDeviceStepDriver
com.example.mobile.login.driver.impl.ios.IOSDeviceStepDriver
com.example.mobile.mobile.login.driver.impl.android.AndroidDeviceStepDriver
```

## 目录结构示例

```
src/test/
├── java/com/example/mobile/login/
│   ├── steps/
│   │   └── UserLoginSteps.java
│   └── driver/
│       ├── DeviceStepDriver.java
│       └── impl/
│           ├── common/
│           │   └── CommonDeviceStepDriver.java
│           ├── ios/
│           │   └── IOSDeviceStepDriver.java
│           └── android/
│               └── AndroidDeviceStepDriver.java
└── resources/
    └── features/
        └── EFP-123-user-login.feature
```