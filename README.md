# MultiMusic Album Wall v3

这是可迁移的本地专辑封面壁纸项目。日常显示只需要 Wallpaper Engine；Python 仅用于导入/更新专辑素材。

## 包含内容

- `albums.csv`：统一专辑清单，可用 Excel 编辑。
- `Imports/YouTubeMusic.csv`：上次保存的 YouTube Music 收藏快照。
- `Covers/`：现有专辑封面图片。
- `Wallpaper/`：Wallpaper Engine 网页壁纸及项目配置。
- `update.py`、`update.cmd`：按需处理/更新脚本。

本包不包含账号认证文件、虚拟环境、缓存、QQ 音乐或 Apple Music 导出文件。壁纸动效是独立复刻，不代表与 macOS 原版逐帧一致。

## 在本机或新电脑安装壁纸

1. 将整个 ZIP 解压到一个固定文件夹，例如 `D:\MultiMusic Album Wall`；不要单独移动 `Wallpaper` 或 `Covers` 子文件夹。
2. 从 Steam 启动 Wallpaper Engine 一次。
3. 双击 `install_wallpaper.cmd`。首次运行会在项目内创建 `.venv` 并安装依赖，然后把壁纸复制到 Wallpaper Engine 的本地项目目录并打开它。当前包没有登录认证，因此会使用包内保存的 YouTube Music CSV；日常显示无需 Python。
4. 之后 Wallpaper Engine 可在 Steam 中正常启动并继续显示该壁纸。壁纸设置面板可调行数、动效、方向、切换节奏和封面间距。

若黑屏，可再次双击 `install_wallpaper.cmd` 重新部署并打开项目；也可在 Wallpaper Engine 中重新选择本地项目 `AlbumWall v3 · Local Album Collection`。

## 更新专辑素材

- 双击 `update.cmd`：尝试从本机 YouTube Music 认证刷新收藏，再读取 CSV、去重、复用/获取封面并更新 `albums.csv` 与 `Wallpaper/covers.js`。
- 双击 `install_wallpaper.cmd`：完成上述更新后，同时重新部署并打开 Wallpaper Engine 项目。
- 若尚未在该电脑配置 YouTube Music 认证，脚本会保留已保存的 `Imports/YouTubeMusic.csv`，继续处理现有清单和图片。认证数据保存在本机 `.local/browser.json`，不会随本包迁移。
- 脚本会在 `.local/backups/` 保存 `albums.csv` 备份。Excel 编辑清单前请先关闭 Excel，避免文件被占用。

更新需要 Windows、Python 3.12 和网络连接。`update.cmd` 会在首次运行时自动建立项目虚拟环境并安装 Python 依赖。壁纸本身不依赖 Python。

## 编辑清单

用 Excel 编辑 `albums.csv`。保留原有列，尤其是 `id`、`cover`、`excluded`、`platform_ids` 等字段；将 `excluded` 设为 `1` 可排除专辑。封面路径相对于项目根目录，例如 `Covers/xxxx.jpg`。脚本会保留手动编辑的专辑信息，并在 `.local/backups/` 留存旧清单。

## 常用动效

选择“全部随机（单格 + 整行/整列）”时，单格动效混合出现，行/列滑动只在部分轮次加入；同轮单格会避开被整行/整列占用的封面。单格混合包含 3D 翻转、滑动覆盖、滑动推移，以及“单格滑离”：旧封面沿上下左右之一滑到相邻格上方，覆盖并替换相邻旧封面；原位置露出静止的新封面。每轮数量按当前网格总格数和“每轮切换比例”动态计算，并随机浮动，不固定为 1–6 张。键盘 `Q` 可切换安静模式，空格可立即触发一轮。

翻转底图：`Wallpaper/flip-background.png` 是每个封面格子底层的静态图片，翻转时露出。图片保持比例，四周使用同色背景填满；迁移包和安装脚本会一并复制。

鼠标悬停：所在封面显示轻微呼吸光圈，单格切换、滑离落点和行列滑动都会避开该格。如果移入时动画已开始，本次动画自然完成；鼠标停留期间不再选择该格进行后续切换。无需额外开启；桌面上需能将鼠标事件传递给壁纸。

悬停呼吸框会随封面的翻转、滑动及整行整列移动，不会打断动画；结束后继续跟随选中的同一张封面，并将锁定转移至它的新位置；若选中的旧封面已滑出，则收起外框。

悬停节奏：停留约 80 毫秒后，外框用 380 毫秒淡入；快速掠过不点亮。封面锁定即时生效，呼吸周期约 2.8 秒。
