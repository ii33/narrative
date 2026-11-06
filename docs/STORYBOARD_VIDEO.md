# 导演台预演与正式视频

先在导演台定案本场，再生成或导入本镜风格帧。视频面板先准备实际输入，显示提示词、图片和干净预演视频；准备不会提交服务。点击提交后只发送一次，刷新只查询同一任务。成功视频先回填到原镜头生成记录，再由导演选择入出点及采用方式；不会自动改镜头时长、变速或采用。

## 生成意图与服务

`reference_motion` 使用无编辑标注的草图动画和1至9张风格参考帧。`endpoints` 使用首帧或首尾风格帧；不同时附带参考视频。镜头和关键帧仍按工程毫秒时间编辑，生成时长在准备时按服务范围明确选择。时长、帧率、素材尺寸或大小不受支持时直接给出诊断，不悄悄拉长原镜头。

当前适配 MiniMax H3 V2。接口与模型参数来自[创建任务](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)和[查询任务](https://platform.minimax.io/docs/api-reference/video-generation-v2-query)。服务地址、模型、分辨率和密钥来源在视频服务设置中配置；默认读取 `MINIMAX_API_KEY`。界面输入的密钥仅保存在应用独立配置，不随工程导出或返回给模型。H3使用按量视频API，不能把聊天套餐视为已有视频凭据。

## 局部动画与采用

- `hold` 按静帧时间保持／切换。
- `layers` 叠加透明图层，按绑定场面对象在当前动作时刻的位置和机位投影移动。每层有明确镜内区间、比例与不透明度。
- `camera` 对平面草图作推拉／平移预演，不产生未画出的侧面或真正的三维绕拍。

`render_storyboard_animatic` 导出真实H.264 MP4，可明确选择草图或风格帧、是否混入临时声音。风格帧缺失时不输出冒充风格版的视频；所有编辑标签、编号和箭头均不进入素材。

采用素材由FFmpeg解码检查并复制到工程，不覆盖原文件。`fit=trim` 要求选段与镜头时长相同；`fit=retime` 明确按选段／镜长计算播放速度。采用后全场播放头换算为素材入点与速度；素材丢失时显示错误或明确回退来源。

## Operations、CLI 与 MCP

各入口共用 `set_storyboard_animation`、`render_storyboard_animatic`、`attach_storyboard_video`、`detach_storyboard_video`、`prepare_storyboard_video`、`submit_storyboard_video`、`refresh_storyboard_video`。服务回执由 `read_storyboard_video_receipts` 读取，即使所属镜头已删除也保留实际任务回执，不能因此重复提交。

准备文件是实际审阅的请求快照，提交前核对文件内容及业务依据；正文、调度、机位、风格或关联资产变化会要求重新准备。已成功产物也由 `check_storyboard` 检查依赖变化，只标记复核范围，不自动重绘、重新提交或覆盖已有结果。网络和服务错误不自动恢复，返回失败和可得诊断。
