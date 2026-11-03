# 参与OpenChinaTT

欢迎来自其他学校、机构和俱乐部的改进。请用Issue描述问题、复现步骤与预期行为；较大的功能变更可先讨论设计。

开发前创建Python虚拟环境并安装 `requirements.txt`。提交前运行：

```sh
python -m unittest discover -s tests -v
node --check public/app.js
```

涉及手机界面的改动请验证窄屏和触摸操作；涉及权限、积分、赛果或数据库迁移，请增加能验证实际行为的测试。保持现有数据及兼容性，不要把真实用户数据、登录凭据、`.env`、数据库备份或私有服务器配置提交到仓库。
