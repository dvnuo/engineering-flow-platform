# Image Tool - Image Analysis

Analyze images with the configured image model.

## Usage

```bash
image image="/path/to/image.jpg" prompt="描述这个图片"
image image="https://example.com/image.png" prompt="有什么物体?"
image image="/path/to/screenshot.png" maxBytesMb=5
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| image | string | Yes | Image path or URL |
| prompt | string | No | Analysis prompt |
| maxBytesMb | int | No | Maximum image size (MB) |
| model | string | No | Image model to use |

## Examples

Describe an image:
```
image image="/path/to/photo.jpg" prompt="描述这个图片的内容"
```

Analyze screenshot:
```
image image="/path/to/screenshot.png" prompt="识别图片中的文字"
```

Analyze URL:
```
image image="https://example.com/chart.png" prompt="分析这个图表"
```
