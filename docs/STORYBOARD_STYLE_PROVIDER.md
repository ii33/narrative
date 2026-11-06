# 导演台素描生成与关键帧风格化

未定案时，在关键帧页填写本帧画面描述，选择构图参考及是否简彩，点“生成素描”。程序把本镜正文、创作约定、当时调度、机位投影和关联资产交给图像模型。几何轮廓仅作为内部参考，最终绑定的是服务实际返回的描述型素描；失败不使用几何图代替。配置了机位时，参考图按2.5D投影安排轮廓，但不推断未建模的接触和精确遮挡。

将场定案后，在“关键帧”中选中要转换的草图，点“生成项目风格图”。一项任务渲染一张图。导演台将原始草图、镜头描述、关联的正文和剧本实际采用的项目风格交给本地 ComfyUI。完成后保存草图和风格图，时间轨仍按原定帧点播放。项目风格可在创作约定中设置。

## 本地服务

默认服务为 http://127.0.0.1:8188。内置工作流位于 src/narrative/workflows/storyboard_flux_klein.json，使用 FLUX Klein、qwen3vl_4b_int8_convrot 文本编码器、flux2 VAE、ReferenceMultiLatent 和 SaveImage。需安装模板中具名的模型和对应节点。启动应用前可设置两个环境变量。

| 环境变量 | 值 |
|---|---|
| NARRATIVE_COMFY_URL | 本机 HTTP 地址，支持端口与路径前缀 |
| NARRATIVE_COMFY_WORKFLOW | 自定义 ComfyUI API 格式 JSON 文件路径 |
| NARRATIVE_COMFY_SKETCH_WORKFLOW | 可选独立素描工作流；未设置时与风格化共用工作流 |

自定义模板中，将图像加载节点的图片输入值写成完整字符串 `{{SKETCH}}`，把正向指令的文本输入值写成 `{{STYLE_PROMPT}}`。可选 `{{WIDTH}}` 与 `{{HEIGHT}}` 为接近草图尺寸的 16 像素网格整数。最终保存图像节点的 `_meta.title` 设置为 `Storyboard Output`，单次输出一张同画幅图像。模版中的其他节点、采样、模型与强度设置由工作流提供。

两种任务共用以上占位符：素描任务的 `{{SKETCH}}` 是内部构图参考，`{{STYLE_PROMPT}}` 是实际素描说明。`{{SEED}}` 在每次请求中替换为新种子，实际值写入审计；自定义工作流固定种子时不覆盖。存在额外资产参考图时，工作流须包含唯一连接VAE的 `ReferenceMultiLatent` 节点（`images.image0` 为主参考），其余图像逐张加入多图输入；不支持时明确报错，不丢弃参考图继续生成。

## 状态与交付

仅在服务报告成功、取回最终图像并成功解码后绑定风格图。保存时重新检查分镜修订、正文修订和当前有效风格；等待期间上述信息变动会返回冲突。执行失败保留已经定案的草图及原有绑定。操作摘要记录服务任务编号和工作流摘要，图片复制在本工程的素材目录。

也可以导出包含草图、正文与项目风格的 ZIP，在外部工具制作后手动绑定。生成质量和构图服从实际模型表现，定案锁定输入方案，风格图仍由导演查看。

工具操作为 `generate_storyboard_sketch(unit_id, shot_id, offset_ms, layout, expected_revision, prompt='', simple_color=False)` 与 `render_storyboard_style(unit_id, shot_id, frame_id, expected_revision)`。上传、实际工作流、提交回执、最终历史响应与生成依据写入审计，模型和界面只收到简短回执；服务没有报告用量时记录未知。查询历史和队列只跟踪已提交的任务，网络或执行错误直接返回，不自动重试。服务上传、队列、历史与图片读取遵循 [ComfyUI 本地服务接口](https://docs.comfy.org/development/comfyui-server/comms_routes)。
