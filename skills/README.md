# Skills Directory

## 目录结构

```
skills/
├── __init__.py          # 技能注册模块
├── decorator.py          # @skill 装饰器
├── executor/             # 技能执行器
├── coding_agent/        # 编码代理 (Codex/Claude/Pi)
├── cron/                # 定时任务技能
├── git/                 # Git 操作技能
├── github-skill/         # GitHub API 技能
├── git-skill/           # Git 封装技能
├── skill_creator/       # 技能创建工具
├── summarize/            # 文本总结技能
├── test_case_generator/ # 测试用例生成
└── test_case_generator/ # 测试用例生成
```

## 工作原理

### 1. 技能注册 (@skill 装饰器)
```python
from skills.decorator import skill, SkillResult

@skill
def my_skill(param1: str, param2: int = 10) -> SkillResult:
    """技能描述"""
    return SkillResult(success=True, output="结果")
```

### 2. 技能执行流程
```
用户请求 → skill() 装饰器 → 技能注册表 → executor 执行 → 返回 SkillResult
```

### 3. SkillResult 结构
```python
class SkillResult:
    success: bool      # 是否成功
    output: str        # 输出内容
    error: str         # 错误信息
    data: dict         # 额外数据
```

## 解决问题

- **代码复用**: 通过 @skill 装饰器标准化技能定义
- **统一接口**: 所有技能返回 SkillResult
- **动态执行**: executor 根据名称调用对应技能
- **参数验证**: 技能函数签名定义参数类型

## 配置方式

技能通常从配置文件或环境变量读取配置：

```python
import config

def my_skill() -> SkillResult:
    api_key = config.get("api.key")
    # 使用配置
```

## 运行方式

### 单独测试技能
```bash
pytest tests/test_*.py -v
```

### 通过 executor 调用
```python
from skills.executor import execute_skill

result = execute_skill("skill_name", param1="value")
```

## 开发原则

### 1. 技能命名规范
- 使用 snake_case: `my_skill`, `git_commit`
- 前缀表示功能: `github_*`, `git_*`

### 2. 函数签名
- 明确参数类型
- 提供默认值
- 返回 `SkillResult`

### 3. 错误处理
```python
@skill
def safe_skill() -> SkillResult:
    try:
        # 业务逻辑
        return SkillResult(success=True, output="ok")
    except Exception as e:
        return SkillResult(success=False, error=str(e))
```

### 4. 文档要求
- 编写 SKILL.md (复杂技能)
- 函数 docstring
- 参数说明

### 5. 测试覆盖
- 单元测试
- 边界条件测试
- 错误场景测试

## 现有技能列表

| 技能名称 | 功能描述 | 状态 |
|---------|---------|------|
| coding_agent | 编码代理执行 | ✅ 完成 |
| git_* | Git 操作 | ✅ 完成 |
| github_* | GitHub API | ✅ 完成 |
| summarize | 文本总结 | ✅ 完成 |
| test_case_generator | 测试生成 | ✅ 完成 |
| skill_creator | 技能创建 | ✅ 完成 |

## 扩展开发

### 创建新技能
1. 在 `skills/` 下创建目录
2. 编写 `skill.py`
3. 使用 `@skill` 装饰函数
4. 创建 `SKILL.md` 文档
5. 编写测试

### 参考示例
- `skills/coding_agent/` - 完整技能示例
- `skills/decorator.py` - 装饰器实现
