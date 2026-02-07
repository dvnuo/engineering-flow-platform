# Tests Directory

## 目录结构

```
tests/
├── __init__.py
├── test_agent*.py       # Agent 测试
├── test_*.py           # 各模块测试
└── conftest.py         # pytest 配置
```

## 工作原理

使用 pytest 进行自动化测试：
- **单元测试**: 测试单个函数/类
- **集成测试**: 测试模块间交互
- **E2E 测试**: 端到端功能测试

## 解决的问题

- **回归测试**: 防止功能退化
- **代码质量**: 提升代码可维护性
- **CI/CD**: 自动化质量检查

## 运行方式

### 运行所有测试
```bash
pytest
```

### 运行特定测试
```bash
pytest tests/test_agent_core.py -v
```

### 生成覆盖率报告
```bash
pytest --cov=. --cov-report=html
```

## 开发原则

### 1. 测试命名
```python
def test_function_name():
    """测试用例说明"""
    pass
```

### 2. 测试组织
- 每个模块对应一个 test_*.py
- 使用 conftest.py 共享 fixture

### 3. 测试原则
- 每个新功能必须添加测试
- 测试覆盖率 > 80%
- 测试应当快速执行
