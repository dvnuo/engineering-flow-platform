---
name: java-cucumber-generator
description: Generate Java Cucumber BDD tests with feature files, step definitions, interfaces, and services
version: 1.0.0
owner: dev-team
triggers:
  - generate cucumber tests
  - generate bdd tests
  - create cucumber
  - java cucumber
  - bdd testing
output_format: json
steps:
  - id: parse_requirements
    title: Parse Requirements
    objective: Extract testable requirements from user input to understand what to test
    instructions:
      - Analyze the user's request carefully
      - Identify the feature/scenario being tested
      - Determine the expected behaviors
      - Identify test data requirements
      - Extract any given/when/then structure mentioned
      - Do NOT generate code in this step
    allowed_tools: []
    completion_check:
      - artifacts.feature_name
      - artifacts.scenarios
    next_step: generate_feature

  - id: generate_feature
    title: Generate Feature File
    objective: Create Cucumber feature file with Gherkin syntax
    instructions:
      - Use the requirements from previous step
      - Create a .feature file in Gherkin format
      - Include Feature, Scenario, Given, When, Then, And, But keywords
      - Use scenario outline for data-driven tests if needed
      - Add meaningful descriptions and context
      - Return JSON with feature file content in artifacts
    allowed_tools: []
    completion_check:
      - artifacts.feature_file
      - artifacts.feature_path
    next_step: generate_steps

  - id: generate_steps
    title: Generate Step Definitions
    objective: Create Cucumber step definition Java class
    instructions:
      - Create a Step Definitions class (e.g., Steps.java)
      - Use @Given, @When, @Then annotations
      - Implement glue code that connects Gherkin to implementation
      - Use Cucumber Expressions or regular expressions for step matching
      - Include @Entity or test data classes if needed
      - Follow Cucumber best practices
      - Return JSON with step definitions content
    allowed_tools: []
    completion_check:
      - artifacts.steps_file
      - artifacts.steps_path
    next_step: generate_interface

  - id: generate_interface
    title: Generate Interface
    objective: Create Java interface for the test service
    instructions:
      - Create a Java interface (e.g., MyServiceInterface.java)
      - Define method signatures that match the test scenarios
      - Use meaningful method names (e.g., calculateTotal, validateInput)
      - Include Javadoc comments
      - Follow Java naming conventions
      - Return JSON with interface content
    allowed_tools: []
    completion_check:
      - artifacts.interface_file
      - artifacts.interface_path
    next_step: generate_service

  - id: generate_service
    title: Generate Service Implementation
    objective: Create Java service implementation class
    instructions:
      - Create a service implementation class
      - Implement the interface from previous step
      - Add @Service or @Component annotation
      - Include basic business logic stub
      - Add appropriate imports
      - Return JSON with service implementation content
    allowed_tools: []
    completion_check:
      - artifacts.service_file
      - artifacts.service_path
    next_step: generate_runner

  - id: generate_runner
    title: Generate Test Runner
    objective: Create Cucumber test runner class
    instructions:
      - Create a Cucumber test runner (e.g., TestRunner.java)
      - Use @RunWith(Cucumber.class)
      - Configure @CucumberOptions with feature and glue paths
      - Include proper package structure
      - Return JSON with runner content
    allowed_tools: []
    completion_check:
      - artifacts.runner_file
      - artifacts.runner_path
    next_step: null
---

# Java Cucumber BDD Test Generator

This skill generates complete Java Cucumber BDD test suites.

## Workflow Steps

### Step 1: Parse Requirements
- Analyzes user input
- Extracts feature, scenarios, test data
- Outputs: `artifacts.feature_name`, `artifacts.scenarios`

### Step 2: Generate Feature File
- Creates .feature file with Gherkin syntax
- Includes Feature, Scenario, Given/When/Then
- Outputs: `artifacts.feature_file`, `artifacts.feature_path`

### Step 3: Generate Step Definitions
- Creates Steps.java with @Given/@When/@Then
- Implements glue code
- Outputs: `artifacts.steps_file`, `artifacts.steps_path`

### Step 4: Generate Interface
- Creates Java interface for service
- Defines method signatures
- Outputs: `artifacts.interface_file`, `artifacts.interface_path`

### Step 5: Generate Service
- Creates service implementation
- Implements interface
- Outputs: `artifacts.service_file`, `artifacts.service_path`

### Step 6: Generate Runner
- Creates TestRunner.java
- Configures Cucumber options
- Outputs: `artifacts.runner_file`, `artifacts.runner_path`

## Usage Examples

```
generate cucumber tests for user login
generate bdd tests for shopping cart checkout
create cucumber tests for password validation
java cucumber for account registration
```

## Expected Step Output

```json
{
  "status": "success",
  "summary": "Generated Feature file for login functionality",
  "artifacts": {
    "feature_name": "User Login",
    "feature_file": "Feature: User Login\n  Scenario: Successful login...",
    "feature_path": "src/test/resources/features/Login.feature",
    "scenarios": ["Successful login", "Failed login with wrong password"]
  },
  "next_step": "generate_steps"
}
```

## Generated File Structure

```
src/test/
├── java/com/example/
│   ├── steps/
│   │   └── LoginSteps.java
│   ├── service/
│   │   ├── LoginService.java (interface)
│   │   └── LoginServiceImpl.java
│   └── runner/
│       └── TestRunner.java
└── resources/
    └── features/
        └── Login.feature
```

## Cucumber Best Practices

1. One step definition class per feature
2. Use Scenario Outline for data-driven tests
3. Keep steps small and focused
4. Use meaningful step text
5. Put business logic in service layer
6. Use Page Objects for UI tests
