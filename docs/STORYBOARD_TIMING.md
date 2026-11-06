# 分镜一期时间与调度合同

业务统一经 `Operations.read_storyboard(unit_id)`、`apply_storyboard_changes(unit_id, revision, changes)` 与 `preview_storyboard(unit_id, time_ms)`。修改要求当前 storyboard revision，整个批次校验并原子入库、记录审计；已定案方案须显式解除定案。无自动迁移或修补。

## 界面操作

画面和二维调度板并排，可分别放大。播放、拖动播放头、逐帧和选段循环共用场剪辑时间；调度板同时显示换算后的动作时间。镜头轨提供缩小／放大、连续缩放滑条及“适配全场”。按住Ctrl滚轮缩放时，鼠标下的时间保持在原位；按钮和滑条优先围绕可见播放头缩放，否则围绕视口中心。普通滚轮继续滚动，正在拖动关键帧或子轨节点时暂不缩放，避免更改拖动坐标。缩放仅为工作区视图状态，不修改镜头时长或工程数据；镜头及子轨共用同一比例。拖动轨内静帧标记只移动该帧的镜内时间。

选中场面对象后开启“记录关键点”，确认显示的对象和动作时刻再拖动或记录朝向；未开启记录时拖动不会写入。对象基础属性与当前动作关键点分别编辑，避免把一次站位调整误作全场基础值。缩短镜头时逐项选择移动或删除越界帧，再与时长一次原子保存；写入冲突保留编辑内容，不自动接受截断。

## 时间

- board 保存 `fps`（整数 1..120，初始24）。全部时间字段保存毫秒整数，帧率仅用于逐帧换算。
- 镜头依列表顺序累计剪辑区间 `[start,end)`；`duration_ms>0`，`action_start_ms>=0`，镜内时间 `local_ms` 对应共同动作时间 `action_start_ms+local_ms`。
- 恰在切点选下一镜；总片尾选最后镜，`local_ms=duration_ms`，仍显示最后已到达的素描帧。超过总长的预览请求钳制在片尾；负数或非整数拒绝。
- 关键帧在 `0<=offset_ms<duration_ms` 内，首帧以前无画面，之后保持最近已到达帧。移动、排序不更改编号。
- 缩短镜头时按整个批次结束后的状态检查。允许同批移动或删除冲突帧后缩短，不截断帧；错误包含镜头、帧编号及冲突毫秒。

## 场面对象及关键点

board.objects 每项：`id,name,kind,parent_id,asset_id,x,y,rotation,depth,state,keyframes`。

- `kind` 必填，`person|prop|set`；`name` 非空。
- `x,y` 是本场平面局部坐标，有限数值 `0..1`，不是屏幕像素。根对象局部坐标即世界坐标。
- `rotation` 为世界平面的局部角度，`depth` 为局部深度；二者有限数值。旋转线性插值直接使用数值差，不取最短角，因此可以表达多圈旋转。
- `parent_id` 可空，否则须为同场对象且无循环。父对象世界旋转作用于子对象局部 `(x,y)`，再加父对象世界位置；旋转与深度逐层相加。围绕父对象坐标原点，无额外中心偏移；派生世界坐标可以超出0..1。
- `asset_id` 可空，否则必须能经当前工程实际资产服务解析。删除仍被子对象引用的父对象会被拒绝，可同批解除引用再删除。
- `state` 为有限JSON对象，程序不推断语义。

动作关键点字段：`id,time_ms,x,y,rotation,depth,state,interpolation`。同对象时间不重复，时间可超过当前剪辑总长，因为后续镜头可取用同一表演。

`hold` 保持到下一点；`linear` 连续插值位置、旋转及深度。区间使用左侧点的 interpolation；state 到下一点才切换。首点之前使用对象基础值。新点未填属性从该动作时刻的当前**局部**派生状态采样，并继承此前点的过渡模式；编辑现有点则仅覆盖明确字段。

## 操作

`changes` 各项均必填 `op`，未知字段拒绝。

- `set_fps {fps}`
- `create_shot {title,duration_ms?,source_block_ids?,action_start_ms?}`；`update_shot {shot_id,...}`
- `move_keyframe {shot_id,frame_id,offset_ms}`；既有 set/delete_keyframe、move/delete_shot 不变。
- `create_object {name,kind,parent_id?,asset_id?,x?,y?,rotation?,depth?,state?}`
- `update_object {object_id,...对象字段}`；`delete_object {object_id}`
- `set_action_keyframe {object_id,time_ms,x?,y?,rotation?,depth?,state?,interpolation?}`，同时间点更新原ID，否则创建新ID。
- `move_action_keyframe {object_id,keyframe_id,time_ms}`；`delete_action_keyframe {object_id,keyframe_id}`。

## 确定性预览

`preview_storyboard(unit_id,time_ms)` 返回 `unit_id,revision,fps,time_ms,total_duration_ms,at_end,shot_id,shot_index,local_ms,action_ms,frame_id,frame,objects`。

无镜头时镜头/画面/时间映射字段为null、objects为空。objects 保留局部值及身份属性，额外包含 `world_x,world_y,world_rotation,world_depth`；不包含编辑关键点数组。预览不写数据，不推进修订。

前端可按相同函数纯投影连续播放；权威实现位于 `src/narrative/storyboard_timing.py` 的 `preview` 与 `object_states`。

# 第二期：共享时刻与摄影机

## 命名时刻与同步

board.markers 为 `[{id:moment-N,name,time_ms}]`，使用共同动作毫秒时钟。

- 镜头可设置 `action_start_marker_id`，其 `action_start_ms` 从标记派生，剪辑位置不变。
- 素描帧可通过 `bind_keyframe_marker` 设置 `marker_id`；其镜内offset从标记动作时间减镜头动作起点派生。
- 动作关键点可通过 `bind_action_keyframe_marker` 设置 `marker_id`；其time_ms从标记派生。
- 绑定值为null即解除同步，保留解除前的实际时间。绑定状态下直接move帧/动作点会拒绝，应选择移动标记或解除绑定。
- `update_marker` 保留标记、帧、动作点身份；自由定位项保持不动。删除仍被引用标记、循环或跨场引用均拒绝。
- `duration_locked` 为镜头时长锁定标识。程序从不为了移动标记自动伸缩任何镜头，锁定与未锁定的越界都明确报错；可明确在同批修改时长/移动/解除绑定后一次保存。

`preview_storyboard_changes(unit_id,revision,changes)` 只模拟已有对象更新/移动/删除/绑定，不分配ID，不持久写入。返回：

- `valid,conflicts`：收集全部超出镜头区间的frame/camera/sound，附shot_id、id、offset_ms、duration_ms、duration_locked；其它结构错误也显式返回。
- `affected_shots:[{shot_id,title,action_start_ms}]`
- `affected_frames:[{shot_id,frame_id,offset_ms}]`
- `affected_objects:[{object_id,name}]`
- `preview`：合法时返回模拟board，否则null。共享动作对象改动保守列出全部可能取用它的镜头与帧，不伪称进行了语义像素差异判断。

## 2.5D坐标与摄影机

对象新增 `height`（默认0.2，正数）与 `elevation`（默认0，有限数值）。depth保持视觉叠放层，不作为高度。世界elevation按父链相加；height不随父对象旋转缩放。

shot.camera 为：

`{preset,target_object_id,subject_size_ratio,keys:[{id:camera-N,offset_ms,x,y,z,yaw,pitch,fov_deg,interpolation}]}`

摄影机xy对应地面坐标，z对应高度。yaw0朝地面+y，正yaw转向+x；pitch正向上；fov_deg是竖直视野角 `[1,179)`，pitch在 `[-90,90]`。镜内关键点同样采用hold/linear，镜内时间必须小于duration_ms；切换、移动保留ID。

preview.camera 包括 `offset_ms,action_ms,position,yaw,pitch,fov_deg,frustum,projections,projection_model`。projection里每项为 `object_id,screen_x,screen_y,size_ratio,visible,occluded,depth`。画面坐标0..1，摄影机后方坐标/占比null。视锥用16:9透视。occluded仅依主体包围形状及远近粗略判断，projection_model明确标识无mesh近似，不能推断手与物接触或复杂遮挡。

## 预设与编辑

`set_camera_preset {shot_id,preset,target_object_id?,start_ms?,end_ms?,amount?,start?:pose,end?:pose}`。

preset可取fixed/dolly/truck/crane/pan/zoom/follow/orbit/reveal/dolly_zoom。start/end是局部镜内区间，默认0及duration_ms-1；姿态仅包含xyzyawpitchfov_deg字段。amount在推进/横移/升降/揭示中为位移，在摇摄/变焦/环绕中为角度。基础orbit限制±360度，自动展开所需中间摄影机点并按极坐标插值，避免沿弦穿过主体。

follow/orbit/reveal/dolly_zoom必须明确同场目标。follow使用目标在实际动作时刻的位移跟随；目标朝向由确定性look-at求得。dolly_zoom根据主体height、摄影机距离与已记录subject_size_ratio连续求fov，以保持主体画面占比。该功能是几何预演，不是实际镜头畸变或成片质量保证。

预设展开后仍是稳定ID的可编辑摄影机点：`set_camera_keyframe`、`move_camera_keyframe`、`delete_camera_keyframe`。手动点可编辑position/angles/fov及hold/linear；跟随和主体比例约束仍由当前preset定义。

项目预设通过 `load_storyboard_camera_presets()` 读取、`save_storyboard_camera_preset(name,settings,expected_revision,reason)` 保存，具有独立修订和审计。settings仅含preset/amount/start/end，不保存场内目标对象编号或时间区间。使用时选定本场目标并传给set_camera_preset。更新同名预设保留可读ID。

素描生成依据包含此刻camera_view；构图投影只是内部参考，最终描述型素描仍须由图像服务生成，不以几何结果替代。

## 只读复核与生成来源

`Operations.check_storyboard(unit_id)` 不写库、不改旧图、不发请求。返回 `ok,needs_review,diagnostics,affected_shots,affected_frames,generations`：error使ok为false，依据变化warning保持结构ok但needs_review为true。generations逐条给出status、basis_status（current/changed/unavailable）及stale（依据无法解析时null），成功产物也不会跳过当前依据比较。

检查覆盖本镜正文引用、同场对象/摄影机/时刻引用、草图与风格图、动画图层、音频实际文件及摘要、采用视频实际可解码时长、生成输入/输出文件与当前生成依据。源块缺失、文件缺失、依据无法计算均报告独立诊断，不以过期状态替换或丢弃原始产物。

新生成帧保存 `sketch_source:{user_prompt,layout,simple_color,provider,job_id,body_revision,asset_ids}`，重开可恢复绘制要求；完整提示及服务原始响应仍仅保存在事务审计。手动替换草图清除旧生成来源。内部sketch_basis_hash用于确定性复核，不是重建提示；复核比较排除全场body_revision，只比较本镜真实引用块，写入并发检查仍保留完整body_revision。

视频签名同样对实际关联块与资产计算；场内共享objects/markers采取保守全场依赖，这会扩大局部复核提示，但不是复杂遮挡或像素语义识别。程序不自动重生成，也不增设人工接受门槛。

## 速度曲线、手持与对焦叠加

摄影机关键点新增 `easing:linear|ease_in|ease_out|ease_in_out`，默认linear。hold不插值；linear区间按左关键点的easing重映射比例t：in为t²，out为1-(1-t)²，inout在t<0.5时2t²，否则1-2(1-t)²。此比例用于同区间姿态、镜头视野、焦距及环绕弧线插值。

关键点 `focus_distance>0`，默认1。新增关键点未填姿态时取该时刻当前摄影机局部姿态，并继承左点的interpolation/easing；只改焦距不会把机位重置或把线性移动停住。

`set_camera_overlays {shot_id,handheld?,focus_target_object_id?}` 独立于运镜预设与原关键点；缺字段保留原值，null解除对应叠加。

handheld为 `{amplitude:0..0.05,frequency_hz:0.1..20,seed:0..2147483647}`。先计算基础运镜及其朝向，再以镜内毫秒t确定性叠加：u=2πft/1000、p=(seed%10007)*0.61803398875；x加a*sin(u+p)，y加a*0.5*cos(1.31u+0.7p)，z加a*0.5*sin(0.79u+1.3p)，yaw加20a*sin(0.67u+1.7p)，pitch加12a*cos(0.83u+1.1p)。不使用随机状态、不会累积漂移，不改原关键点。

focus_target_object_id须为同场对象。叠加手持后，单独取目标中心到摄影机的欧氏距离作为当前focus_distance，不改变视线。删除仍被对焦引用的对象需同批明确解除引用。preview.camera返回focus_distance及 `focus_model:2.5d_distance_band_not_optical_dof`；projection.in_focus仅在可见对象中心距离处于焦距±max(0.01,焦距*0.1)带内为true。这是导演视图近似标志，不是物理景深或真实模糊渲染。

set_camera_preset可同时接收easing/handheld；不会清除已独立设置的focus_target。项目预设settings可保存easing、handheld及start/end中的focus_distance，不保存任何场内目标ID。
