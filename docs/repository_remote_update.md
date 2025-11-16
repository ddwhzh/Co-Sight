# 仓库地址更新记录

## 1 变更背景

原始仓库位于 `https://github.com/ZTE-AICloud/Co-Sight.git`，为统一访问路径并匹配当前 SSH 访问要求，已将远程地址切换为 `ssh://git@ssh.github.com:443/ddwhzh/Co-Sight.git`。

## 2 已确认事实（代码/文档中已有）

- `README.md` 的安装说明 “Download the project” 仅保留了新的 SSH 克隆命令，自此官方文档与远程配置保持一致。
- `README-zh.md` “下载项目”节同步更新到同一 SSH 地址，替换原有 HTTP/SSH 双路说明。
- 本地 git 配置（非提交文件）通过 `git remote set-url origin ...` 命令指向新的 SSH 地址。

## 3 预测与建议

- 预测：后续所有开发者或自动化流程均应以 `ssh://git@ssh.github.com:443/ddwhzh/Co-Sight.git` 作为唯一克隆入口，避免多个源地址导致同步错误。
- 建议：若未来需要开放其他协议（如 HTTP），应在 `docs/` 中新增补充文档并显式标注为“辅助访问”而非主通道。

## 4 注意事项

- 本文档为事实驱动的变更记录，所有列出的项目均已在代码/配置中实际完成，无额外假设逻辑。
