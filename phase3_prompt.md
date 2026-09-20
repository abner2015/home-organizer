现在实现图片上传系统。

使用MinIO/S3兼容对象存储。

要求：

1. 后端接收图片。
2. 校验Content-Type。
3. 校验文件大小。
4. 生成唯一Object Key。
5. 上传到MinIO。
6. 数据库保存图片metadata。
7. API不能暴露MinIO内部凭证。
8. 图片访问使用安全的签名URL或后端代理。
9. 前端不能获得MinIO Access Key。
10. 支持JPEG/PNG/WebP。
11. 防止恶意文件上传。
12. 文件名不能直接作为Object Key。
13. 增加上传测试。
14. 增加异常处理。
15. 增加Docker配置。

实现：

POST /api/v1/assets/upload

返回：

asset_id
url或signed_url
content_type
size

完成后运行全部测试。
