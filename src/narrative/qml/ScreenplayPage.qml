import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: page
    objectName: structureOnly ? "screenplayStructurePage" : "screenplayPage"
    property var app
    property bool structureOnly:false
    readonly property var t:app.theme
    property var workspace:({screenplays:[],adaptation:{nodes:[]},timeline:{nodes:[]},document:{}})
    readonly property var doc:workspace.document || ({})
    readonly property var screenplay:doc.screenplay || ({})
    readonly property var storage:workspace.storage || ({})
    readonly property bool storageNeedsReset:storage.status==="incomplete"
    readonly property bool canCreateScreenplay:!storageNeedsReset||storage.can_initialize_on_create===true
    readonly property var segments:doc.segments || []
    property string selectedSegmentId:""
    property string selectedUnitId:""
    readonly property var segment:segments.find(function(s){return s.id===selectedSegmentId}) || ({})
    readonly property var units:segment.units || []
    readonly property var unit:units.find(function(u){return u.id===selectedUnitId}) || ({})
    property var rangePreview:({})
    property int rangePreviewRequest:0
    property bool pendingRangeRefresh:false
    readonly property bool hasTimelineNodes:((workspace.timeline||{}).nodes||[]).length>0
    property string pendingSpanName:""
    property var pendingRebind:({})
    property string notice:""
    property var exportResult:({})
    property string exportFormat:"text"
    property var writingTarget:({})
    property string writingTaskId:""
    property string writingStatus:""
    property var submittedUnitIds:[]
    property var structureEdit:({})
    property bool structureBusy:false
    property bool storageResetBusy:false
    property string storageResetFingerprint:""
    property string storageResetSummary:""
    property var timelineImportCandidates:[]
    property var timelineImportSelection:[]
    property var timelineImportCollapsed:({})
    property var timelineImportScope:({})
    readonly property bool screenplaySwitching:!!app.screenplayId&&app.screenplayId!==screenplay.id
    readonly property var timelineImportRows:{
        var rows=[],known={},seen={}
        timelineImportCandidates.forEach(function(row){known[row.id]=row})
        function walk(row,depth){
            if(seen[row.id])return
            seen[row.id]=true;rows.push(Object.assign({},row,{depth:depth}))
            if(!page.timelineImportCollapsed[row.id])page.timelineImportCandidates.filter(function(child){return child.parent_id===row.id}).sort(function(a,b){return a.sort_id-b.sort_id}).forEach(function(child){walk(child,depth+1)})
        }
        timelineImportCandidates.filter(function(row){return !row.parent_id||!known[row.parent_id]}).sort(function(a,b){return a.sort_id-b.sort_id}).forEach(function(row){walk(row,0)})
        return rows
    }
    readonly property var outlineLabels:({planning:"规划中",adopted:"已采用",changed:"有修改"})
    readonly property var basisLabels:({unbased:"依据未记录",current:"依据一致",changed:"依据需复查",invalid:"来源失效",empty_source:"来源为空"})
    readonly property var bodyLabels:({empty:"正文为空",present:"已有正文"})
    readonly property var spanLabels:({valid:"有效",invalid:"失效",empty:"为空"})
    readonly property var blockLabels:({action:"动作",dialogue:"对白",screen_text:"字幕",sound:"声音",transition:"转场"})
    readonly property var targetLabels:({screenplay:"整部剧本",episode:"集",act:"幕",segment:"段落",scene_table:"场表",unit:"场次",blocks:"正文块"})
    readonly property var taskLabels:({running:"进行中",cancelled:"已停止",failed:"未完成",completed:"已完成"})
    readonly property string sceneTablePrompt:"请根据本段绑定范围编写或按我的意见修改场级场表。同一连续时空默认一场；情绪、招式、胜负、镜头变化不能单独拆场，事件和节拍不是场。只写场级剧情概要、时空、人物及必要状态连续性，不生成分镜、特效方案或镜头要求。多场须基于实际时空或叙事层切换；已有场次优先复用ID并保留正文。"
    function diagnosticsText(values){return (values||[]).map(function(value){return value.message || value.code || String(value)}).join("\n")}
    function storageCountsText(){
        var counts=storage.row_counts||{}
        return [["screenplays","剧本"],["script_episodes","集"],["script_acts","幕"],["script_segments","段落"],["script_units","场次"],["script_blocks","正文块"],["script_timelines","时间线副本"],["adaptation_spans","时间段"],["script_structure_entries","结构归属"],["script_writing_bases","写作依据"],["script_entity_usages","实体引用"]].map(function(pair){return pair[1]+" "+(counts[pair[0]]||0)}).join(" · ")
    }
    function openStorageReset(){storageResetFingerprint=storage.expected_storage_fingerprint;storageResetSummary=storageCountsText();storageDiscardConfirmed.checked=false;storageResetReason.text="重建不完整的剧本存储";storageResetDialog.open()}
    function resetStorage(){storageResetBusy=true;act("reset_screenplay_storage",{reason:storageResetReason.text,scope:"screenplay",expected_storage_fingerprint:storageResetFingerprint,confirm_discard:true})}
    function unitTitle(value){return (value.scene_number ? "第"+value.scene_number+"场 · " : "")+((value.plan||{}).title || (value.kind==="screen_segment"?"独立画面段":"未命名场次"))}
    function sceneSetting(plan){
        var parts=[{INT:"内景",EXT:"外景",INT_EXT:"内外景",UNSPECIFIED:"内外景未指定"}[plan.interior_exterior]||"内外景未指定"]
        var place=plan.location_text||""
        if(!place&&plan.location_id){var node=((workspace.timeline||{}).nodes||[]).find(function(item){return item.id===plan.location_id});if(node)place=node.name}
        if(place)parts.push(place)
        if(plan.time_label)parts.push(plan.time_label)
        return parts.join(" · ")
    }
    function targetDescription(){
        var ids=writingTarget.ids||[],all=[screenplay].concat(doc.episodes||[],doc.acts||[],segments)
        segments.forEach(function(s){(s.units||[]).forEach(function(u){all.push({id:u.id,title:page.unitTitle(u)});(u.blocks||[]).forEach(function(b,index){all.push({id:b.id,title:(page.blockLabels[b.type]||"正文块")+" "+(index+1)})})})})
        var names=ids.map(function(id){var found=all.find(function(value){return value.id===id});return found?found.title:"所选目标"})
        return (targetLabels[writingTarget.kind]||"写作目标")+" · "+ids.length+" 项\n"+names.slice(0,4).join("、")+(ids.length>4?" 等":"")
    }
    readonly property var entries:doc.structure_entries || []
    readonly property var structureRows: {
        var out=[]
        function walk(kind,id,depth) {
            entries.filter(function(e){return e.parent_kind===kind && e.parent_id===id}).sort(function(a,b){return a.position-b.position}).forEach(function(e){
                var items=e.child_kind==="episode" ? (doc.episodes || []) : e.child_kind==="act" ? (doc.acts || []) : segments
                var object=items.find(function(x){return x.id===e.child_id}) || {title:e.child_id}
                out.push({entry:e,object:object,depth:depth})
                if(e.child_kind!=="segment") walk(e.child_kind,e.child_id,depth+1)
            })
        }
        if(screenplay.id) walk("screenplay",screenplay.id,0)
        return out
    }
    function load() { if(visible && app.mode==="workspace") bridge.loadScreenplayWorkspace(app.screenplayId || "") }
    function act(operation,args) { bridge.screenplayAction(operation,args) }
    function structure(actions,format) { if(structureBusy)return;structureBusy=true;var args={screenplay_id:screenplay.id,actions:actions,expected_structure_revision:screenplay.structure_revision,reason:"编辑剧本结构"}; if(format)args.format=format; act("apply_screenplay_structure",args) }
    function changeSceneTable(change) { act("apply_scene_table_changes",{segment_id:segment.id,changes:[change],expected_outline_revision:segment.outline_revision,reason:"手工编辑场表"}) }
    function changeBlock(change) { act("apply_script_block_changes",{unit_id:unit.id,changes:[change],target_block_ids:change.block_id?[change.block_id]:[],expected_body_revision:unit.body_revision,reason:"手工编辑正文"}) }
    function lastBlockId() { var blocks=unit.blocks || [];return blocks.length?blocks[blocks.length-1].id:null }
    function previousBlock(index) { return index>1 ? unit.blocks[index-2].id:null }
    function selectSegment(id) { selectedSegmentId=id;selectedUnitId="" }
    function resetRangeScope(){
        range.startId="";range.endId="";rangePreview=({});rangePreviewRequest++;pendingRangeRefresh=false
        timelineImportScope=({});timelineImportCandidates=[];timelineImportSelection=[];timelineImportCollapsed=({})
        timelineImportDialog.close()
    }
    function selectScreenplay(id){resetRangeScope();app.screenplayId=id;selectedSegmentId="";selectedUnitId="";load()}
    function openBody(id) {
        selectedUnitId=id;bodyColumn.collapsed=false
        Qt.callLater(function(){workbenchViewport.contentX=Math.max(0,Math.min(workbenchViewport.contentWidth-workbenchViewport.width,bodyColumn.x+bodyColumn.width-workbenchViewport.width))})
    }
    function importTimelineSelection(){
        var scope=timelineImportScope
        if(scope.screenplay_id!==screenplay.id||scope.screenplay_id!==app.screenplayId||scope.timeline_id!==screenplay.timeline_id){resetRangeScope();return}
        act("append_screenplay_timeline",{screenplay_id:scope.screenplay_id,moment_ids:timelineImportSelection.slice(),expected_timeline_revision:scope.timeline_revision,expected_adaptation_revision:scope.adaptation_revision,reason:"追加导入剧本时间点，保留已有内容"})
    }
    function segmentPlacement(id) {
        var entry=entries.find(function(e){return e.child_kind==="segment"&&e.child_id===id}),parts=[]
        while(entry&&entry.parent_kind!=="screenplay") {
            var values=entry.parent_kind==="episode"?(doc.episodes||[]):(doc.acts||[])
            var parent=values.find(function(value){return value.id===entry.parent_id});if(parent)parts.unshift(parent.title)
            var kind=entry.parent_kind,parentId=entry.parent_id
            entry=entries.find(function(e){return e.child_kind===kind&&e.child_id===parentId})
        }
        return parts.length?parts.join(" / "):"幕外"
    }
    function segmentProgress(value) {
        var enabled=(value.units||[]).filter(function(u){return u.enabled!==false&&u.kind==="scene"})
        var done=enabled.filter(function(u){return u.body_presence==="present"}).length
        var seconds=(value.units||[]).reduce(function(sum,u){return sum+(u.plan.estimated_duration_seconds||0)},0)
        return "正文 "+done+"/"+enabled.length+" · 估计 "+seconds+" 秒"
    }
    function moveSegment(id,delta) {
        var entry=entries.find(function(e){return e.child_kind==="segment"&&e.child_id===id});if(!entry)return
        var siblings=entries.filter(function(e){return e.parent_kind===entry.parent_kind&&e.parent_id===entry.parent_id}).sort(function(a,b){return a.position-b.position})
        var index=siblings.findIndex(function(e){return e.id===entry.id}), target=index+delta
        if(target<0||target>=siblings.length)return
        var after=delta<0?(target>0?siblings[target-1].id:null):siblings[target].id
        structure([{op:"move",child_kind:"segment",child_id:id,parent_kind:entry.parent_kind,parent_id:entry.parent_id,after_entry_id:after}])
    }
    function dropStructure(entry,target,before) {
        if(entry.id===target.id)return
        var parentKind=target.parent_kind,parentId=target.parent_id,after=target.id
        if(target.child_kind!=="segment"&&entry.child_kind!=="episode") {parentKind=target.child_kind;parentId=target.child_id;after=null}
        else if(before) {var siblings=entries.filter(function(e){return e.parent_kind===parentKind&&e.parent_id===parentId}).sort(function(a,b){return a.position-b.position});var index=siblings.findIndex(function(e){return e.id===target.id});after=index>0?siblings[index-1].id:null}
        structure([{op:"move",child_kind:entry.child_kind,child_id:entry.child_id,parent_kind:parentKind,parent_id:parentId,after_entry_id:after}])
    }
    function openWriting(kind,ids,prompt) {
        writingTarget={kind:kind,ids:ids.slice(),screenplay_id:screenplay.id}
        if(kind==="unit"||kind==="blocks")writingTarget.include_existing=true
        if(kind==="scene_table"||kind==="segment")writingTarget.segment_id=ids[0]
        if(kind==="unit")writingTarget.unit_id=ids[0]
        if(kind==="blocks"){writingTarget.unit_id=unit.id;writingTarget.segment_id=segment.id}
        writingPrompt.text=prompt || "";writingDialog.open()
    }
    function previewRange() { if(range.startId&&range.endId){rangePreviewRequest++;act("preview_adaptation_span",{screenplay_id:screenplay.id,selection_ids:[range.startId,range.endId],_view_request_token:rangePreviewRequest})} }
    function refreshRetainedRange(){
        pendingRangeRefresh=false
        var nodes=(workspace.timeline||{}).nodes||[]
        if(range.startId&&range.endId&&nodes.some(function(n){return n.id===range.startId&&!n.retracted})&&nodes.some(function(n){return n.id===range.endId&&!n.retracted}))previewRange()
        else {range.startId="";range.endId="";rangePreview=({})}
    }
    function createSegment() {
        pendingRebind=({})
        pendingSpanName=segmentTitle.text.trim()
        act("create_adaptation_span",{name:pendingSpanName,selection_ids:[range.startId,range.endId],screenplay_id:screenplay.id,expected_timeline_revision:workspace.timeline.revision,reason:"选择改编连续范围"})
    }
    function rebindSegment() {
        pendingRebind={id:segment.id,revision:segment.revision}
        act("create_adaptation_span",{name:segment.title,selection_ids:[range.startId,range.endId],screenplay_id:screenplay.id,expected_timeline_revision:workspace.timeline.revision,reason:"重新绑定段落范围并保留已有正文"})
    }
    function adopt() { act("adopt_scene_table",{segment_id:segment.id,expected_outline_revision:segment.outline_revision,adopted:segment.outline_state!=="adopted",reason:"更新场表工作标记"}) }
    function prepareBatch() { act("prepare_script_writing_targets",{screenplay_id:screenplay.id,include_existing:false,include_screen_segments:false}) }
    function changeFormat() {
        var actions=[], isSeries=screenplay.format==="series"
        if(!isSeries) {
            var id="EP-"+Date.now()
            actions.push({op:"create_episode",id:id,title:"第1集",number:"1"})
            entries.filter(function(e){return e.parent_kind==="screenplay"}).sort(function(a,b){return b.position-a.position}).forEach(function(e){actions.push({op:"move",child_kind:e.child_kind,child_id:e.child_id,parent_kind:"episode",parent_id:id,after_entry_id:null})})
        } else {
            var roots=structureRows.filter(function(row){return row.entry.parent_kind==="episode"}).map(function(row){return row.entry}).reverse()
            roots.forEach(function(e){actions.push({op:"move",child_kind:e.child_kind,child_id:e.child_id,parent_kind:"screenplay",parent_id:screenplay.id,after_entry_id:null})})
            ;(doc.episodes||[]).forEach(function(e){actions.push({op:"delete_episode",episode_id:e.id,expected_revision:e.revision,move_to:null})})
        }
        structure(actions,isSeries?"single":"series")
    }
    onVisibleChanged:if(visible)load()
    Connections {
        target:app
        function onModeChanged(){ if(app.mode==="workspace")page.load();else{page.workspace=({});page.selectedSegmentId="";page.selectedUnitId="";page.writingStatus="";page.writingTaskId="";page.submittedUnitIds=[];page.structureBusy=false} }
    }
    Connections {
        target:bridge
        function onResultReady(kind,payload) {
            if(kind==="screenplay"&&payload.operation==="apply_screenplay_structure"&&!page.visible){page.structureBusy=false;return}
            if(kind==="screenplay_workspace"&&page.visible) {
                if(payload.superseded)return
                var nextScreenplay=(payload.document||{}).screenplay||({})
                if(screenplay.id!==nextScreenplay.id||screenplay.timeline_id!==nextScreenplay.timeline_id)resetRangeScope()
                workspace=payload
                if(storageNeedsReset)app.screenplayId=""
                if(screenplay.id)app.screenplayId=screenplay.id
                if(!segments.some(function(s){return s.id===selectedSegmentId}))selectedSegmentId=segments.length?segments[0].id:""
                if(!units.some(function(u){return u.id===selectedUnitId}))selectedUnitId=units.length?units[0].id:""
                if(pendingRangeRefresh)refreshRetainedRange()
            } else if(kind==="screenplay"&&page.visible) {
                var result=payload.result || {}, op=payload.operation
                if(op==="reset_screenplay_storage"){
                    storageResetBusy=false;storageResetDialog.close();app.screenplayId="";selectedSegmentId="";selectedUnitId="";resetRangeScope()
                    notice="剧本存储已重建，原文、改编与资产保持不变。现在可以新建剧本。";page.load();return
                }
                if(["preview_adaptation_span","import_screenplay_timeline_candidates","import_screenplay_timeline","append_screenplay_timeline"].indexOf(op)>=0
                   &&(payload.request_screenplay_id!==screenplay.id||payload.request_screenplay_id!==app.screenplayId))return
                if(op==="apply_screenplay_structure"){
                    // The returned document is authoritative before another
                    // edit can read its structure revision or open a dialog.
                    var updated=Object.assign({},workspace);updated.document=result;workspace=updated
                    structureDialog.close();structureBusy=false
                }
                if(op==="import_screenplay_timeline_candidates"){
                    timelineImportScope={screenplay_id:payload.request_screenplay_id,timeline_id:screenplay.timeline_id,timeline_revision:workspace.timeline.revision,adaptation_revision:result.length?result[0].source_revision:workspace.adaptation.revision,appending:hasTimelineNodes}
                    var localIds=((workspace.timeline||{}).nodes||[]).map(function(node){return node.id})
                    timelineImportCandidates=result.map(function(row){return Object.assign({},row,{imported:row.imported===true||localIds.indexOf(row.id)>=0})});timelineImportSelection=[];timelineImportCollapsed=({});timelineImportDialog.open();return
                }
                if(op==="append_screenplay_timeline"||op==="import_screenplay_timeline"){
                    timelineImportDialog.close();rangePreviewRequest++;rangePreview=({});pendingRangeRefresh=true
                    var added=((result.import_summary||{}).added_node_ids||[]).length
                    var addedProperties=((result.import_summary||{}).added_property_ids||[]).length
                    notice=timelineImportScope.appending?(added||addedProperties?"已追加 "+added+" 个节点"+(addedProperties?"、"+addedProperties+" 条属性记录":"")+"；已有段落、场表和正文保留。":"所选节点均已导入，现有内容保持。"):"已导入剧本独立副本，可继续追加时间点。"
                    page.load();return
                }
                if(op==="preview_adaptation_span") {
                    if(!range.startId||!range.endId||(result.span||{}).timeline_id!==screenplay.timeline_id)return
                    if(JSON.stringify(payload.request_selection_ids)!==JSON.stringify([range.startId,range.endId]))return
                    if(payload.request_token!==rangePreviewRequest)return
                    rangePreview=result;return
                }
                if(op==="create_adaptation_span") {
                    var span=result.span || result
                    if(pendingRebind.id){act("update_script_segment",{segment_id:pendingRebind.id,expected_revision:pendingRebind.revision,changes:{span_id:span.id},reason:"重设段落绑定，保留正文"});pendingRebind=({});return}
                    act("create_script_segment",{screenplay_id:screenplay.id,span_id:span.id,title:pendingSpanName,expected_structure_revision:screenplay.structure_revision,reason:"范围绑定新段落"});return
                }
                if(op==="create_screenplay") { app.screenplayId=(result.screenplay || result).id;createDialog.close() }
                if(op==="create_script_segment")selectedSegmentId=(result.segment || result).id
                if(op==="apply_scene_table_changes"){planEditor.close()}
                if(op==="apply_script_block_changes"){blockEditor.close()}
                if(op==="export_screenplay"){exportResult=result;exportDialog.open();return}
                if(op==="check_screenplay"){notice=(result.ok?"剧本检查通过":"剧本检查结果")+"\n"+diagnosticsText(result.issues||result.diagnostics||[]);return}
                if(op==="prepare_script_writing_targets") {
                    writingTarget={kind:"unit",ids:result.unit_ids || [],screenplay_id:screenplay.id,target_manifest:result}
                    writingPrompt.text="按当前剧本顺序，仅为目标清单中已启用且正文为空的普通场景逐场写作；每场通过校验后提交，保留成功场。";writingDialog.open();return
                }
                page.load()
            } else if(kind==="screenplay_export_saved")app.toast("已导出到 "+payload.path,false)
            else if(kind==="screenplay_writing"){if(writingTaskId!==payload.task_id||!writingStatus){writingTaskId=payload.task_id || "";writingStatus="running";submittedUnitIds=[]}app.assistantOpen=true;writingDialog.close()}
            else if(kind==="conversation_progress"&&payload.writing_target&&payload.writing_target.screenplay_id===page.screenplay.id){
                writingTaskId=payload.task_id || writingTaskId;writingStatus=payload.writing_status==="incomplete"?"failed":payload.writing_status || writingStatus;submittedUnitIds=payload.submitted_unit_ids || submittedUnitIds;page.load()
            }
            else if(kind==="assistant" || (kind==="conversation_progress" && payload.event==="task_complete"))page.load()
        }
        function onErrorOccurred(kind,message){if(kind==="screenplay"){page.structureBusy=false;page.storageResetBusy=false}}
    }
    ColumnLayout {
        anchors.fill:parent;anchors.margins:16;spacing:10
        RowLayout {
            Layout.fillWidth:true
            Label { text:page.structureOnly?"结构":"剧本";font.pixelSize:22;font.bold:true;color:t.ink }
            ComboBox { objectName:"screenplaySelector";enabled:!page.structureBusy;Layout.preferredWidth:260;model:page.workspace.screenplays || [];textRole:"title";currentIndex:Math.max(0,(page.workspace.screenplays||[]).findIndex(function(s){return s.id===page.screenplay.id}));onActivated:page.selectScreenplay(model[currentIndex].id) }
            Button { objectName:"screenplayCreate";text:"新建剧本";enabled:page.canCreateScreenplay;onClicked:createDialog.open() }
            Item { Layout.fillWidth:true }
            Button { text:"刷新";onClicked:page.load() }
            Button { text:"检查";enabled:!!page.screenplay.id;onClicked:page.act("check_screenplay",{screenplay_id:page.screenplay.id}) }
            Button { objectName:"screenplayExport";text:"导出";enabled:!!page.screenplay.id;onClicked:{page.exportFormat="text";page.act("export_screenplay",{screenplay_id:page.screenplay.id,format:"text"})} }
        }
        Label { visible:!!page.notice;text:page.notice;color:t.warn;wrapMode:Text.Wrap;Layout.fillWidth:true }
        Frame {
            objectName:"screenplayStorageWarning";visible:page.storageNeedsReset;Layout.fillWidth:true
            background:Rectangle{color:"#fff7e8";border.color:t.warn;radius:6}
            contentItem:ColumnLayout{
                Label{text:"剧本存储需重建";font.bold:true;color:t.ink}
                Label{objectName:"screenplayStorageCounts";text:page.storageCountsText();color:t.body;wrapMode:Text.Wrap;Layout.fillWidth:true}
                Label{text:"当前剧本存储缺少所需结构。重建将清空剧本及其时间线副本、集幕、段落、场表和正文；原文、源改编和资产不受影响。";color:t.body;wrapMode:Text.Wrap;Layout.fillWidth:true}
                Button{objectName:"screenplayStorageReset";text:"重建剧本存储";enabled:!page.storageResetBusy;onClicked:page.openStorageReset()}
            }
        }
        Label {visible:!!page.writingStatus;text:"当前写作任务："+(page.taskLabels[page.writingStatus]||page.writingStatus)+(page.submittedUnitIds.length?" · 已提交 "+page.submittedUnitIds.length+" 场正文":"");color:page.writingStatus==="failed"?t.warn:t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true;HoverHandler{id:taskHover}ToolTip.visible:taskHover.hovered;ToolTip.text:page.writingTaskId}
        Label { visible:!page.screenplay.id&&!page.storageNeedsReset;text:"创建剧本方案，再从改编时间线导入节点，选择连续范围建立段落。";color:t.muted }
        RowLayout {
            visible:!!page.screenplay.id;Layout.fillWidth:true
            Label { text:page.screenplay.title+" · "+(page.screenplay.format==="series"?"剧集":"单片")+" · 结构修订 "+page.screenplay.structure_revision;color:t.muted }
            Item {Layout.fillWidth:true}
            Button { text:"修改剧本要求";enabled:!page.structureBusy;onClicked:settingsDialog.openFor() }
            Button { text:"讨论整部";onClicked:page.openWriting("screenplay",[page.screenplay.id],"") }
            Button {visible:!page.structureOnly;text:"批量写空场";onClicked:page.prepareBatch()}
            Button {text:page.structureOnly?"回剧本工作页":"集幕结构";onClicked:app.goTo(page.structureOnly?"screenplay":"screenplay_structure")}
        }
        Flickable {
            id:workbenchViewport
            objectName:"scriptWorkbenchViewport"
            visible:!!page.screenplay.id&&!page.structureOnly
            enabled:!page.screenplaySwitching
            Layout.fillWidth:true;Layout.fillHeight:true;clip:true
            contentWidth:Math.max(width,rangeColumn.effectiveMinimumWidth+segmentColumn.effectiveMinimumWidth+sceneColumn.effectiveMinimumWidth+bodyColumn.effectiveMinimumWidth+24)
            contentHeight:height
            boundsBehavior:Flickable.StopAtBounds
            flickableDirection:Flickable.HorizontalFlick
            ScrollBar.horizontal:ScrollBar{policy:workbenchViewport.contentWidth>workbenchViewport.width?ScrollBar.AlwaysOn:ScrollBar.AsNeeded}
            SplitView {
                id:workbenchColumns
                objectName:"scriptWorkbenchColumns"
                width:workbenchViewport.contentWidth;height:workbenchViewport.height
                orientation:Qt.Horizontal
                handle:Rectangle {objectName:"scriptWorkbenchHandle";implicitWidth:6;color:"transparent";MouseArea{anchors.fill:parent;acceptedButtons:Qt.NoButton;cursorShape:Qt.SplitHCursor}}
                ScriptWorkbenchColumn {
                    id:rangeColumn;objectName:"scriptRangeColumn";namePrefix:"scriptRangeColumn";title:"改编时间表";t:page.t;expandedWidth:260;minimumExpandedWidth:230
                    Button {objectName:"scriptTimelineImport";text:page.hasTimelineNodes?"追加导入时间点":"从改编时间线导入";Layout.fillWidth:true;onClicked:page.act("import_screenplay_timeline_candidates",{screenplay_id:page.screenplay.id})}
                    Label {visible:!((page.workspace.timeline||{}).nodes||[]).length;text:"尚未导入时间点。导入后保留剧本独立副本。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.muted}
                    Label {visible:((page.workspace.timeline||{}).nodes||[]).length>0;text:"已导入的剧本独立副本";font.pixelSize:11;Layout.fillWidth:true;color:t.muted}
                ScriptRangePicker {id:range;objectName:"scriptRangePicker";Layout.fillWidth:true;Layout.fillHeight:true;nodes:(page.workspace.timeline||{}).nodes || [];t:page.t;onSelectionChanged:page.previewRange()}
                Label { objectName:"scriptRangeStatus";Layout.fillWidth:true;wrapMode:Text.Wrap;color:page.rangePreview.status==="valid"?t.ok:t.warn;text:page.rangePreview.status ? "范围"+(page.spanLabels[page.rangePreview.status]||"")+" · 有效 "+(page.rangePreview.effective_node_ids || []).length+" · 屏蔽 "+(page.rangePreview.suppressed_count || 0)+(page.diagnosticsText(page.rangePreview.diagnostics)?"\n"+page.diagnosticsText(page.rangePreview.diagnostics):"") : "选择范围起止，容器将展开到事件/动作" }
                RowLayout {Layout.fillWidth:true;TextField{id:segmentTitle;objectName:"scriptSegmentTitle";placeholderText:"新段落名称";Layout.fillWidth:true}Button{objectName:"scriptCreateSegment";text:"建立段落";enabled:!!segmentTitle.text.trim()&&page.rangePreview.status==="valid";onClicked:page.createSegment()}}

                }
                ScriptWorkbenchColumn {
                    id:segmentColumn;objectName:"scriptSegmentColumn";namePrefix:"scriptSegmentColumn";title:"段落表";t:page.t;expandedWidth:240;minimumExpandedWidth:220
                    Label {text:page.segment.title || "选择段落";font.pixelSize:16;color:t.ink;Layout.fillWidth:true;elide:Text.ElideRight}
                    Flow {Layout.fillWidth:true;spacing:4;Button{text:"段落设置";enabled:!!page.segment.id;onClicked:segmentDialog.openFor()}Button{implicitWidth:32;Layout.preferredWidth:32;text:"↑";enabled:!!page.segment.id&&!page.structureBusy;onClicked:page.moveSegment(page.segment.id,-1)}Button{implicitWidth:32;Layout.preferredWidth:32;text:"↓";enabled:!!page.segment.id&&!page.structureBusy;onClicked:page.moveSegment(page.segment.id,1)}}
ListView {
                    objectName:"scriptSegmentTable";Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:page.segments
                    delegate:ItemDelegate {
                        required property var modelData
                        width:ListView.view.width;highlighted:page.selectedSegmentId===modelData.id
                        background:Rectangle{color:page.selectedSegmentId===modelData.id?t.accentSoft:t.card;border.color:t.line;radius:4}
                        contentItem:Column {
                            Label{text:modelData.title+(modelData.enabled===false?"（停用）":"");color:t.ink;font.bold:true}
                            Label{text:"场数 "+(modelData.units||[]).length+" · "+(page.outlineLabels[modelData.outline_state]||"规划中")+" · "+(page.basisLabels[modelData.outline_basis_state]||"依据未记录");color:t.muted;font.pixelSize:11}
                            Label{text:page.segmentPlacement(modelData.id)+" · "+page.segmentProgress(modelData);color:t.muted;font.pixelSize:11}
                            Label{text:"目标 "+(modelData.target_duration_seconds || "—")+" 秒 · 来源"+(page.spanLabels[(modelData.span||{}).status] || "待绑定");color:t.muted;font.pixelSize:11}
                            Label{text:((modelData.span||{}).member_node_ids||[]).length+" 个时间点 · 屏蔽 "+((modelData.span||{}).suppressed_count||0);color:t.muted;font.pixelSize:11}
                        }
                        onClicked:page.selectSegment(modelData.id)
                    }
                }
                }
                ScriptWorkbenchColumn {
                    id:sceneColumn;objectName:"scriptSceneColumn";namePrefix:"scriptSceneColumn";title:"场表";t:page.t;expandedWidth:340;minimumExpandedWidth:300
ColumnLayout {visible:!!page.segment.id;Layout.fillWidth:true;Button{objectName:"scriptWriteSceneTable";text:"讨论 / 拆场";onClicked:page.openWriting("scene_table",[page.segment.id],page.sceneTablePrompt) }Button{objectName:"scriptAdoptOutline";text:page.segment.outline_state==="adopted"?"取消采用标记":"采用当前场表";onClicked:page.adopt()}Label{text:"采用仅是工作标记，可继续修改";font.pixelSize:11;color:t.muted}}
RowLayout {visible:!!page.segment.id;Layout.fillWidth:true;Button{objectName:"scriptAddUnit";text:"新增场次 / 画面段";onClicked:planEditor.openFor(null)}}
ListView {
                    objectName:"scriptUnitTable";Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:page.units
                    delegate:Frame {
                        required property var modelData
                        required property int index
                        width:ListView.view.width
                        height:unitCard.implicitHeight+24
                        ColumnLayout {id:unitCard;anchors.fill:parent
                            RowLayout {Layout.fillWidth:true;Label{text:(modelData.scene_number||String(index+1))+" · "+(modelData.plan.title||(modelData.kind==="screen_segment"?"独立画面段":"未命名场次"));color:t.ink;font.bold:true;Layout.fillWidth:true;Layout.minimumWidth:0;elide:Text.ElideRight}CheckBox{text:"启用";checked:modelData.enabled!==false;onClicked:page.changeSceneTable({op:"update",unit_id:modelData.id,changes:{enabled:checked}})}}
                            Label{text:(page.bodyLabels[modelData.body_presence]||"正文为空")+" · "+(page.basisLabels[modelData.basis_state]||"依据未记录");color:t.muted;font.pixelSize:10;HoverHandler{id:unitHover}ToolTip.visible:unitHover.hovered;ToolTip.text:modelData.id}
                            Label{text:page.sceneSetting(modelData.plan);color:t.body;wrapMode:Text.Wrap;Layout.fillWidth:true}
                            Label{text:modelData.plan.summary;wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.body}
                            Flow{Layout.fillWidth:true;spacing:6;Button{text:"编辑场表";onClicked:{page.selectedUnitId=modelData.id;planEditor.openFor(modelData)}}Button{objectName:"scriptOpenBody";text:"打开正文";onClicked:{page.openBody(modelData.id)}}Button{implicitWidth:32;Layout.preferredWidth:32;text:"↑";enabled:index>0;onClicked:page.changeSceneTable({op:"move",unit_id:modelData.id,after_id:index>1?page.units[index-2].id:null})}Button{text:"删除";onClicked:{page.selectedUnitId=modelData.id;deleteUnitDialog.open()}}}
                        }
                    }
                }
                }
                ScriptWorkbenchColumn {
                    id:bodyColumn;objectName:"scriptBodyColumn";namePrefix:"scriptBodyColumn";title:"正文";t:page.t;expandedWidth:400;minimumExpandedWidth:340
                    RowLayout {Layout.fillWidth:true;Button{text:"写当前场";enabled:!!page.unit.id;onClicked:page.openWriting("unit",[page.unit.id],"请按本场计划写正文；只修改明确目标场，校验并自动提交。") }Button{text:"修改当前场";enabled:!!page.unit.id;onClicked:page.openWriting("unit",[page.unit.id],"")}}

                    
                    ComboBox {objectName:"scriptBodyUnit";Layout.fillWidth:true;model:page.units.map(function(u){return {id:u.id,title:page.unitTitle(u)}});textRole:"title";displayText:page.unit.id?page.unitTitle(page.unit):"选择场次";currentIndex:Math.max(0,page.units.findIndex(function(u){return u.id===page.selectedUnitId}));onActivated:page.selectedUnitId=model[currentIndex].id}
                    Label {text:page.unit.id?page.unitTitle(page.unit)+" · "+(page.bodyLabels[page.unit.body_presence]||"正文为空")+" · "+(page.basisLabels[page.unit.basis_state]||"依据未记录"):"";color:t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true;HoverHandler{id:bodyUnitHover}ToolTip.visible:bodyUnitHover.hovered;ToolTip.text:page.unit.id||""}
                    Button {objectName:"scriptAddBlock";text:"添加正文块";enabled:!!page.unit.id;onClicked:blockEditor.openFor(null)}
                    ListView {objectName:"scriptBodyBlocks";Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:page.unit.blocks || []
                        delegate:Frame {required property var modelData;required property int index;width:ListView.view.width
                            height:bodyCard.implicitHeight+24
                            ColumnLayout {id:bodyCard;anchors.fill:parent
                                Label{text:(page.blockLabels[modelData.type]||"正文块")+" "+(index+1);color:t.muted;font.pixelSize:10;HoverHandler{id:blockHover}ToolTip.visible:blockHover.hovered;ToolTip.text:modelData.id}
                                Label{visible:modelData.type==="dialogue";text:(modelData.payload.speaker_label||"未命名发声者")+" · "+({on_screen:"现场",off_screen:"画外",voice_over:"旁白",inner_voice:"内心声"}[modelData.payload.delivery]||"");color:t.ink;font.bold:true}
                                TextArea {objectName:"scriptBlockDisplayText";text:modelData.payload.text || modelData.payload.kind || "";readOnly:true;selectByMouse:true;wrapMode:TextEdit.Wrap;Layout.fillWidth:true;background:null}
                                Flow{Layout.fillWidth:true;spacing:6;Button{text:"修改";onClicked:blockEditor.openFor(modelData)}Button{text:"对话修改此块";onClicked:page.openWriting("blocks",[modelData.id],"")}Button{implicitWidth:32;Layout.preferredWidth:32;text:"↑";enabled:index>0;onClicked:page.changeBlock({op:"move",block_id:modelData.id,after_id:page.previousBlock(index)})}Button{text:"删除";onClicked:page.changeBlock({op:"delete",block_id:modelData.id})}}
                            }
                        }
                    }
                
                }
            }
        }
        ColumnLayout {
            visible:page.structureOnly&&!!page.screenplay.id;Layout.fillWidth:true;Layout.fillHeight:true
            enabled:!page.structureBusy&&!page.screenplaySwitching
            RowLayout{Button{objectName:"scriptAddEpisode";text:"新增集";visible:page.screenplay.format==="series";onClicked:structureDialog.openFor("episode",null)}Button{objectName:"scriptAddAct";text:"新增幕";onClicked:structureDialog.openFor("act",null)}Button{text:page.screenplay.format==="series"?"重组为单片":"重组为剧集";onClicked:formatDialog.open()}Label{text:"集 / 幕 / 段落只维护一套权威归属与次序";color:t.muted}}
            ListView {id:structureList;objectName:"scriptStructureTree";Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:page.structureRows
                delegate:Frame {id:structureRow;objectName:"scriptStructureRow";required property var modelData;required property int index;width:ListView.view.width;height:54
                    RowLayout {anchors.fill:parent;Label{objectName:"scriptStructureDragHandle";text:(modelData.entry.child_kind==="episode"?"集":modelData.entry.child_kind==="act"?"幕":"段落")+" · "+modelData.object.title;Layout.leftMargin:modelData.depth*24;Layout.fillWidth:true;color:t.ink
                        MouseArea {anchors.fill:parent;preventStealing:true;cursorShape:Qt.SizeVerCursor;property real pressY:0;property bool moved:false;onPressed:function(mouse){pressY=mouse.y;moved=false}onPositionChanged:function(mouse){if(pressed&&Math.abs(mouse.y-pressY)>8)moved=true}onReleased:function(mouse){if(!moved)return;var target=structureList.indexAt(20,structureRow.y+mouse.y);if(target>=0&&target!==structureRow.index)page.dropStructure(modelData.entry,page.structureRows[target].entry,target<structureRow.index)}}
                    }Button{text:"讨论";onClicked:page.openWriting(modelData.entry.child_kind,[modelData.object.id],"")}Button{text:"设置 / 移动";onClicked:structureDialog.openFor(modelData.entry.child_kind,modelData.object)}Button{text:"↑";onClicked:structureDialog.moveSibling(modelData.entry,-1)}Button{text:"↓";onClicked:structureDialog.moveSibling(modelData.entry,1)}}
                }
            }
        }
    }
    ScriptPlanEditor{id:planEditor;host:page}
    ScriptBlockEditor{id:blockEditor;host:page}
    Dialog {
        id:storageResetDialog;objectName:"screenplayStorageResetDialog";title:"确认重建剧本存储";modal:true;anchors.centerIn:parent;width:Math.min(680,page.width-40)
        contentItem:ColumnLayout {
            enabled:!page.storageResetBusy
            Label{text:page.storageResetSummary;wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.ink}
            Label{text:"这会清空当前工程全部剧本、独立时间线副本、集幕、段落、场次、正文和写作依据，再建立当前结构。原文、源改编、资产及其他工程保持不变。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.body}
            TextField{id:storageResetReason;objectName:"screenplayStorageResetReason";placeholderText:"重建原因";Layout.fillWidth:true}
            CheckBox{id:storageDiscardConfirmed;objectName:"screenplayStorageDiscard";text:"我确认清空上述剧本域内容并重建";checked:false}
            RowLayout{Item{Layout.fillWidth:true}Button{objectName:"screenplayStorageResetCancel";text:"取消";onClicked:storageResetDialog.close()}Button{objectName:"screenplayStorageResetConfirm";text:"确认清空并重建";enabled:storageDiscardConfirmed.checked&&!!storageResetReason.text.trim();onClicked:page.resetStorage()}}
        }
    }
    Dialog {
        id:timelineImportDialog;objectName:"scriptTimelineImportDialog";title:page.timelineImportScope.appending?"追加导入时间点":"从改编时间线导入独立副本";modal:true;anchors.centerIn:parent;width:Math.min(720,page.width-40);height:Math.min(620,page.height-30)
        contentItem:ColumnLayout {
            Label {text:page.timelineImportScope.appending?"新时间点将追加到剧本时间表；已有节点、段落、场表和正文保留。可选择已导入的容器，追加其新增子项。":"选择时间点及其子项，所需人物、地点和属性一并导入。剧本保留独立副本，源改编后续变化不会自动覆盖。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.body}
            ListView {
                objectName:"scriptTimelineImportTree";Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:page.timelineImportRows
                delegate:RowLayout {
                    required property var modelData
                    width:ListView.view.width
                    ToolButton {text:page.timelineImportCollapsed[modelData.id]?"+":"-";visible:(modelData.children||[]).length>0;Layout.leftMargin:(modelData.depth||0)*16;onClicked:{var folded=Object.assign({},page.timelineImportCollapsed);folded[modelData.id]=!folded[modelData.id];page.timelineImportCollapsed=folded}}
                    CheckDelegate {
                        objectName:"scriptTimelineImportChoice";property string nodeId:modelData.id
                        Layout.fillWidth:true;Layout.leftMargin:(modelData.children||[]).length?0:(modelData.depth||0)*16
                        text:({storyline:"主要故事",big_event:"大事件",scene:"场景",event:"事件",action:"动作"}[modelData.node_type]||"时间点")+" · "+modelData.name+(modelData.imported?"（已导入）":"")+(modelData.suppressed?"（屏蔽）":"");checked:page.timelineImportSelection.indexOf(modelData.id)>=0
                        onToggled:{var selected=page.timelineImportSelection.slice();if(checked&&selected.indexOf(modelData.id)<0)selected.push(modelData.id);if(!checked)selected=selected.filter(function(id){return id!==modelData.id});page.timelineImportSelection=selected}
                    }
                }
            }
            RowLayout {Layout.fillWidth:true;Label{text:"已选 "+page.timelineImportSelection.length+" 项";color:t.muted}Item{Layout.fillWidth:true}Button{text:"取消";onClicked:timelineImportDialog.close()}Button{objectName:"scriptTimelineImportConfirm";text:page.timelineImportScope.appending?"追加所选":"导入所选";enabled:page.timelineImportSelection.length>0;onClicked:page.importTimelineSelection()}}
        }
    }
    Dialog {
        id:createDialog;objectName:"scriptCreateDialog";title:"新建剧本方案";modal:true;anchors.centerIn:parent;width:420
        contentItem:ColumnLayout {TextField{id:createTitle;objectName:"scriptCreateTitle";placeholderText:"剧本名称";Layout.fillWidth:true}ComboBox{id:createFormat;model:["单片","剧集"]}Button{objectName:"scriptCreateSave";text:"创建";enabled:!!createTitle.text.trim()&&page.canCreateScreenplay;onClicked:page.act("create_screenplay",{title:createTitle.text,format:createFormat.currentIndex===1?"series":"single",reason:"创建剧本方案"})}}
    }
    Dialog {
        id:deleteUnitDialog;title:"删除场次";modal:true;anchors.centerIn:parent;standardButtons:Dialog.Ok|Dialog.Cancel
        contentItem:ColumnLayout{Label{text:"删除“"+page.unitTitle(page.unit)+"”。已有正文需要明确丢弃。"}CheckBox{id:discardBody;text:"确认丢弃本场正文"}}
        onAccepted:page.changeSceneTable({op:"delete",unit_id:page.unit.id,body_policy:discardBody.checked?"discard":"require_empty"})
    }
    Dialog {
        id:writingDialog;objectName:"scriptWritingDialog";title:"在当前聊天中写作 / 修改";modal:true;anchors.centerIn:parent;width:Math.min(700,page.width-40);height:420
        contentItem:ColumnLayout {
            Label{objectName:"scriptWritingTarget";text:"明确目标："+page.targetDescription();wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.accent}
            CheckBox{id:showTargetIds;text:"显示目标定位编号";checked:false}
            Label{objectName:"scriptWritingTargetIds";visible:showTargetIds.checked;text:(page.writingTarget.ids||[]).join("\n");wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.muted;font.pixelSize:11}
            Label{text:"仅修改本次固定目标；后续页面选择不会改变已发出的任务。";color:t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
            CheckBox {text:page.writingTarget.target_manifest?"固定批量：仅正文为空的启用普通场景":"允许修改目标范围内已有正文";checked:page.writingTarget.include_existing===true;enabled:!page.writingTarget.target_manifest;onToggled:{var target=Object.assign({},page.writingTarget);target.include_existing=checked;page.writingTarget=target}}
            TextArea{id:writingPrompt;objectName:"scriptWritingPrompt";placeholderText:"写作或修改要求";wrapMode:TextEdit.Wrap;Layout.fillWidth:true;Layout.fillHeight:true}
            RowLayout{Item{Layout.fillWidth:true}Button{text:"取消";onClicked:writingDialog.close()}Button{objectName:"scriptWritingSend";text:"发送到当前聊天";enabled:!!writingPrompt.text.trim()&&(page.writingTarget.ids||[]).length>0;onClicked:{app.assistantOpen=true;bridge.screenplayWritingTurn(writingPrompt.text,page.writingTarget)}}}
        }
    }
    Dialog {
        id:exportDialog;objectName:"scriptExportDialog";title:"剧本导出预览";modal:true;anchors.centerIn:parent;width:Math.min(900,page.width-40);height:Math.min(700,page.height-30)
        contentItem:ColumnLayout{RowLayout{ComboBox{model:["文本","JSON"];currentIndex:page.exportFormat==="json"?1:0;onActivated:{page.exportFormat=currentIndex===1?"json":"text";page.act("export_screenplay",{screenplay_id:page.screenplay.id,format:page.exportFormat})}}Button{text:"保存 UTF-8 文件";onClicked:exportFile.open()}}Label{visible:(page.exportResult.diagnostics||[]).length>0;text:page.diagnosticsText(page.exportResult.diagnostics);color:t.warn;wrapMode:Text.Wrap;Layout.fillWidth:true}ScrollView{Layout.fillWidth:true;Layout.fillHeight:true;TextArea{objectName:"scriptExportText";text:page.exportResult.text || "";readOnly:true;selectByMouse:true;wrapMode:TextEdit.Wrap}}}
    }
    FileDialog{id:exportFile;fileMode:FileDialog.SaveFile;defaultSuffix:page.exportFormat==="json"?"json":"txt";onAccepted:bridge.saveScreenplayExport(page.screenplay.id,page.exportFormat,selectedFile.toString())}
    Dialog{id:formatDialog;title:"完整重组结构";modal:true;anchors.centerIn:parent;standardButtons:Dialog.Ok|Dialog.Cancel;contentItem:Label{text:page.screenplay.format==="series"?"将各集下的幕和段落搬到单片根级，并删除空集。":"创建第1集，并将当前根级幕和段落全部归入此集。";wrapMode:Text.Wrap;width:420}onAccepted:page.changeFormat()}
    ScriptStructureEditor{id:structureDialog;host:page}
    ScriptSegmentEditor{id:segmentDialog;host:page}
    ScriptSettingsEditor{id:settingsDialog;host:page}
}





