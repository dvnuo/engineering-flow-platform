# File Naming Conventions

## Naming Principles

1. **CamelCase** - Java files use CamelCase
2. **Kebab-case** - Feature files use hyphens
3. **Semantic clarity** - File names should express the functionality

## File Naming Rules

### Feature Files
```
{ticket}-{feature-name}.feature
```
- ticket: Jira ticket ID (e.g., EFP-123)
- feature-name: Feature name (kebab-case)

**Examples**:
- `EFP-123-user-login.feature`
- `EFP-456-product-search.feature`

### Java Files

#### Step Definitions
```
{FeatureName}Steps.java
```
**Examples**:
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
**Examples**:
- `CommonDeviceStepDriver.java`
- `IOSDeviceStepDriver.java`
- `AndroidDeviceStepDriver.java`

## Package Naming Conventions

```
{groupId}.mobile.{module}.steps
{groupId}.mobile.{module}.driver
{groupId}.mobile.{module}.driver.impl.common
{groupId}.mobile.{module}.driver.impl.ios
{groupId}.mobile.{module}.driver.impl.android
```

**Example** (groupId=com.example, module=login):
```
com.example.mobile.login.steps.UserLoginSteps
com.example.mobile.login.driver.DeviceStepDriver
com.example.mobile.login.driver.impl.common.CommonDeviceStepDriver
com.example.mobile.login.driver.impl.ios.IOSDeviceStepDriver
com.example.mobile.login.driver.impl.android.AndroidDeviceStepDriver
```

## Directory Structure Example

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