import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: page
    SubmissionState{id:contentSubmission;owner:page;operation:"create_adaptation_span";target:({screenplay_id:page.screenplay.id||null});onRestoreRequested:function(a){segmentTitle.text=a.name||"";page.pendingSpanName=a.name||"";range.startId=(a.selection_ids||[])[0]||a.start_node_id||"";range.endId=(a.selection_ids||[]).slice(-1)[0]||a.end_node_id||"";page.previewRange()}}

    objectName: structureOnly ? "screenplayStructurePage" : "screenplayPage"
    property var app
    property bool structureOnly:false
    property bool restoringSelection:false
    onSelectedSegmentIdChanged:captureSelection()
    onSelectedUnitIdChanged:captureSelection()
    onSelectedBlockIdChanged:captureSelection()
    property string selectedBlockId:""
    property bool blockActionBusy:false
    property int blockActionSequence:0
    property var blockActionScope:({})
    readonly property var selectedBlock:(unit.blocks||[]).find(function(b){return b.id===selectedBlockId})||({})
    readonly property int selectedBlockIndex:(unit.blocks||[]).findIndex(function(b){return b.id===selectedBlockId})
    onUnitChanged:if(selectedBlockId&&!(unit.blocks||[]).some(function(b){return b.id===selectedBlockId}))selectedBlockId=""
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
    property string exportFormat:"pdf"
    property string exportScreenplayId:""
    readonly property var exportFormats:[{id:"pdf",label:"PDF 文档",extension:"pdf",filter:"PDF 文档 (*.pdf)"},{id:"docx",label:"Word 文档",extension:"docx",filter:"Word 文档 (*.docx)"},{id:"html",label:"HTML 网页",extension:"html",filter:"HTML 网页 (*.html)"},{id:"text",label:"纯文本",extension:"txt",filter:"文本文件 (*.txt)"},{id:"fountain",label:"Fountain 剧本",extension:"fountain",filter:"Fountain 剧本 (*.fountain)"},{id:"json",label:"结构化 JSON",extension:"json",filter:"JSON 文件 (*.json)"}]
    readonly property var exportChoice:exportFormats.find(function(row){return row.id===exportFormat})||exportFormats[0]
    property var writingTarget:({})
    property string writingTaskId:""
    property string writingStatus:""
    property var submittedUnitIds:[]
    property var structureEdit:({})
    property bool timelineBusy:false
    readonly property string timelineSelectedId:range.selectedId
    property var timelineMutationScope:({})
    property string pendingTimelineSelection:""
    property var timelineDeleteScope:({})
    property bool structureBusy:false
    property bool storageResetBusy:false
    property string storageResetFingerprint:""
    property string storageResetSummary:""
    property var timelineImportCandidates:[]
    property var timelineImportSelection:[]
    property var timelineImportScope:({})
    readonly property bool screenplaySwitching:!!app.screenplayId&&app.screenplayId!==screenplay.id
    readonly property var timelineImportRows: timelineImportCandidates
    readonly property var outlineLabels:({planning:"规划中",adopted:"已采用",changed:"有修改"})
    readonly property var basisLabels:({unbased:"依据未记录",current:"依据一致",changed:"依据需复查",invalid:"来源失效",empty_source:"来源为空"})
    readonly property var bodyLabels:({empty:"正文为空",present:"已有正文"})
    readonly property var spanLabels:({valid:"有效",invalid:"失效",empty:"为空"})
    readonly property var blockLabels:({action:"动作",scene_description:"场景描写",dialogue:"对白",screen_text:"字幕",sound:"声音",transition:"转场"})
    readonly property var targetLabels:({screenplay:"整部剧本",episode:"集",act:"幕",segment:"段落",scene_table:"场表",unit:"场次",blocks:"正文块"})
    readonly property var taskLabels:({running:"进行中",cancelled:"已停止",failed:"未完成",completed:"已完成"})

    property var effectivePrompts: ({})
    readonly property string sceneTablePrompt: effectivePrompts.scene_table_shortcut || ""
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
    function timelineAction(operation,id,expectedRevision) {
        timelineMutationScope={screenplay_id:screenplay.id,timeline_id:screenplay.timeline_id,revision:expectedRevision===undefined?workspace.timeline.revision:expectedRevision}
        timelineBusy=true
        act(operation,{screenplay_id:screenplay.id,node_id:id,expected_timeline_revision:timelineMutationScope.revision,reason:operation==="delete_screenplay_timeline_node"?"物理删除剧本时间点及子树":"复制剧本时间点及子树"})
    }
    function structure(actions,format) { if(structureBusy)return;structureBusy=true;var args={screenplay_id:screenplay.id,actions:actions,expected_structure_revision:screenplay.structure_revision,reason:"编辑剧本结构"}; if(format)args.format=format; act("apply_screenplay_structure",args) }
    function changeSceneTable(change) { act("apply_scene_table_changes",{segment_id:segment.id,changes:[change],expected_outline_revision:segment.outline_revision,reason:"手工编辑场表"}) }
    function changeBlock(change) {
        if(blockActionBusy||!unit.id)return
        var rows=unit.blocks||[],index=rows.findIndex(function(b){return b.id===change.block_id})
        if(change.block_id&&index<0)return
        blockActionSequence++;blockActionBusy=true
        blockActionScope={token:"body-action-"+(structureOnly?"structure":"writing")+"-"+blockActionSequence,screenplay_id:screenplay.id,unit_id:unit.id,block_id:change.block_id,replacement_id:rows[index+1]?rows[index+1].id:index>0?rows[index-1].id:""}
        act("apply_script_block_changes",{unit_id:unit.id,changes:[change],target_block_ids:change.block_id?[change.block_id]:[],expected_body_revision:unit.body_revision,reason:"手工编辑正文",_view_request_token:blockActionScope.token})
    }
    function moveSelectedBlock(){var index=selectedBlockIndex;if(index>0)changeBlock({op:"move",block_id:selectedBlock.id,after_id:index>1?unit.blocks[index-2].id:null})}
    function lastBlockId() { var blocks=unit.blocks || [];return blocks.length?blocks[blocks.length-1].id:null }
    function previousBlock(index) { return index>1 ? unit.blocks[index-2].id:null }
    function selectSegment(id) { selectedSegmentId=id;selectedUnitId="" }
    function resetRangeScope(){
        range.startId="";range.endId="";rangePreview=({});rangePreviewRequest++;pendingRangeRefresh=false
        timelineImportScope=({});timelineImportCandidates=[];timelineImportSelection=[]
        timelineImportDialog.close();timelineEditor.close();timelineDeleteDialog.close();timelineBusy=false;timelineMutationScope=({});pendingTimelineSelection=""
    }
    function captureSelection() {
        if(restoringSelection||!visible||!screenplay.id||screenplay.id!==app.screenplayId)return
        if(selectedUnitId&&!units.some(function(u){return u.id===selectedUnitId}))return
        app.screenplaySelection={project_path:app.project.path||"",screenplay_id:screenplay.id,segment_id:selectedSegmentId,unit_id:selectedUnitId,block_id:(unit.blocks||[]).some(function(b){return b.id===selectedBlockId})?selectedBlockId:""}
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
    onVisibleChanged:if(visible){load();bridge.loadPromptSettings()}
    Connections {
        target:app
        function onProjectChanged(){page.blockActionBusy=false;page.blockActionScope=({});page.restoringSelection=true;page.workspace=({});page.selectedSegmentId="";page.selectedUnitId="";page.selectedBlockId="";page.restoringSelection=false;page.effectivePrompts=({});if(app.mode==="workspace"){bridge.loadPromptSettings()}}
        function onModeChanged(){ if(app.mode==="workspace")page.load();else{page.workspace=({});page.selectedSegmentId="";page.selectedUnitId="";page.writingStatus="";page.writingTaskId="";page.submittedUnitIds=[];page.structureBusy=false} }
    }
    Connections {
        target:bridge
        function onResultReady(kind,payload) {
            if(kind==="screenplay"&&payload.operation==="apply_script_block_changes"&&payload.request_token===page.blockActionScope.token)page.blockActionBusy=false
            if(kind==="prompt_settings"){
                if(payload.project_path!==(app.project.path||""))return
                var effective={};(payload.prompts||[]).forEach(function(row){effective[row.id]=row.effective});page.effectivePrompts=effective;return
            }
            if(kind==="screenplay"&&payload.operation==="apply_screenplay_structure"&&!page.visible){page.structureBusy=false;return}
            if(kind==="screenplay_workspace"&&page.visible) {
                if(payload.superseded)return
                var nextScreenplay=(payload.document||{}).screenplay||({})
                if(screenplay.id!==nextScreenplay.id||screenplay.timeline_id!==nextScreenplay.timeline_id)resetRangeScope()
                var shared=app.screenplaySelection||{},restore=shared.screenplay_id===nextScreenplay.id&&shared.project_path===(app.project.path||"")
                restoringSelection=true
                workspace=payload
                timelineBusy=false
                if(pendingTimelineSelection&&((workspace.timeline||{}).nodes||[]).some(function(node){return node.id===pendingTimelineSelection})){range.reveal(pendingTimelineSelection);pendingTimelineSelection=""}
                if(storageNeedsReset)app.screenplayId=""
                if(screenplay.id)app.screenplayId=screenplay.id
                if(restore&&segments.some(function(s){return s.id===shared.segment_id}))selectedSegmentId=shared.segment_id
                if(!segments.some(function(s){return s.id===selectedSegmentId}))selectedSegmentId=segments.length?segments[0].id:""
                if(restore&&units.some(function(u){return u.id===shared.unit_id}))selectedUnitId=shared.unit_id
                if(!units.some(function(u){return u.id===selectedUnitId}))selectedUnitId=units.length?units[0].id:""
                selectedBlockId=restore&&(unit.blocks||[]).some(function(b){return b.id===shared.block_id})?shared.block_id:""
                restoringSelection=false;captureSelection()
                if(selectedBlockId)Qt.callLater(function(){bodyBlocksList.positionViewAtIndex(bodyBlocksList.currentIndex,ListView.Contain)})
                if(pendingRangeRefresh)refreshRetainedRange()
            } else if(kind==="screenplay"&&page.visible) {
                var result=payload.result || {}, op=payload.operation
                if(op==="apply_script_block_changes"&&payload.request_token===page.blockActionScope.token&&page.screenplay.id===page.blockActionScope.screenplay_id&&page.selectedUnitId===page.blockActionScope.unit_id&&page.selectedBlockId===page.blockActionScope.block_id){var blocks=result.blocks||[];if(!blocks.some(function(b){return b.id===page.selectedBlockId}))page.selectedBlockId=blocks.some(function(b){return b.id===page.blockActionScope.replacement_id})?page.blockActionScope.replacement_id:""}
                if(["load_creative_agreement","save_creative_agreement","load_compile_settings","save_compile_settings","list_assets"].indexOf(op)>=0)return
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
                    timelineImportCandidates=result.map(function(row){return Object.assign({},row,{imported:row.imported===true||localIds.indexOf(row.id)>=0})});timelineImportSelection=[];timelineImportDialog.open();return
                }
                if(op==="append_screenplay_timeline"||op==="import_screenplay_timeline"){
                    timelineImportDialog.close();rangePreviewRequest++;rangePreview=({});pendingRangeRefresh=true
                    var added=((result.import_summary||{}).added_node_ids||[]).length
                    var addedProperties=((result.import_summary||{}).added_property_ids||[]).length
                    notice=timelineImportScope.appending?(added||addedProperties?"已追加 "+added+" 个节点"+(addedProperties?"、"+addedProperties+" 条属性记录":"")+"；已有段落、场表和正文保留。":"所选节点均已导入，现有内容保持。"):"已导入剧本独立副本，可继续追加时间点。"
                    var importDiagnostics=(result.import_summary||{}).diagnostics||[]
                    if(importDiagnostics.length)notice+="\n"+importDiagnostics.map(function(row){return (row.code==="source_interval_conflict"?"来源已有属性区间冲突，请核对：":"")+(row.message||row.code||"")}).join("\n")
                    page.load();return
                }
                if(["edit_screenplay_timeline_node","move_screenplay_timeline_node","copy_screenplay_timeline_node","delete_screenplay_timeline_node"].indexOf(op)>=0){
                    var scope=timelineMutationScope
                    if(payload.request_screenplay_id!==screenplay.id||scope.screenplay_id!==screenplay.id||scope.timeline_id!==screenplay.timeline_id)return
                    timelineEditor.close();timelineDeleteDialog.close()
                    pendingTimelineSelection=result.created_node_id||result.copied_node_id||range.selectedId
                    rangePreviewRequest++;rangePreview=({});pendingRangeRefresh=true
                    if(op==="delete_screenplay_timeline_node"){
                        var removed=result.deleted_node_ids||[]
                        if(removed.indexOf(range.startId)>=0)range.startId=""
                        if(removed.indexOf(range.endId)>=0)range.endId=""
                        if(removed.indexOf(range.selectedId)>=0){range.selectedId="";pendingTimelineSelection=""}
                        notice="已删除 "+removed.length+" 个时间点及其子树；已有段落和正文保留。"
                        var issues=[];(result.affected_spans||[]).forEach(function(span){issues=issues.concat(span.diagnostics||[])})
                        if(issues.length)notice+="\n"+diagnosticsText(issues)
                    }else notice=op==="copy_screenplay_timeline_node"?"已复制时间点及全部下属。":op==="move_screenplay_timeline_node"?"时间点及子树已移动。":"剧本时间点已保存。"
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
                if(op==="compile_screenplay"){if(payload.request_screenplay_id!==exportScreenplayId||exportScreenplayId!==screenplay.id)return;exportResult=result;exportDialog.open();return}
                if(op==="check_screenplay"){notice=(result.ok?"剧本检查通过":"剧本检查结果")+"\n"+diagnosticsText(result.issues||result.diagnostics||[]);return}
                if(op==="prepare_script_writing_targets") {
                    writingTarget={kind:"unit",ids:result.unit_ids || [],screenplay_id:screenplay.id,target_manifest:result}
                    writingPrompt.text=page.effectivePrompts.batch_writing_shortcut || "";writingDialog.open();return
                }
                page.load()
            } else if(kind==="screenplay_export_saved")app.toast("已导出到 "+payload.path,false)
            else if(kind==="screenplay_writing"){if(writingTaskId!==payload.task_id||!writingStatus){writingTaskId=payload.task_id || "";writingStatus="running";submittedUnitIds=[]}app.assistantOpen=true;writingDialog.close()}
            else if(kind==="conversation_progress"&&payload.writing_target&&payload.writing_target.screenplay_id===page.screenplay.id){
                writingTaskId=payload.task_id || writingTaskId;writingStatus=payload.writing_status==="incomplete"?"failed":payload.writing_status || writingStatus;submittedUnitIds=payload.submitted_unit_ids || submittedUnitIds;page.load()
            }
            else if(kind==="assistant" || (kind==="conversation_progress" && payload.event==="task_complete"))page.load()
        }
        function onErrorOccurred(kind,message){if(kind==="screenplay"){page.blockActionBusy=false;page.structureBusy=false;page.storageResetBusy=false;page.timelineBusy=false}}
    }
    ColumnLayout {
        SubmissionFeedback{state:contentSubmission}

        anchors.fill:parent;anchors.margins:16;spacing:10
        Flickable {
            id:headerViewport;objectName:"screenplayHeaderViewport"
            Layout.fillWidth:true;Layout.preferredHeight:headerActions.implicitHeight+(contentWidth>width?10:0)
            contentWidth:Math.max(width,headerActions.implicitWidth);contentHeight:headerActions.implicitHeight
            flickableDirection:Flickable.HorizontalFlick;boundsBehavior:Flickable.StopAtBounds;clip:true
            ScrollBar.horizontal:ScrollBar{policy:headerViewport.contentWidth>headerViewport.width?ScrollBar.AlwaysOn:ScrollBar.AlwaysOff}
            RowLayout {
                id:headerActions;objectName:"screenplayPrimaryActions";width:headerViewport.contentWidth;spacing:4
                Label {objectName:"screenplayHeaderTitle";text:page.structureOnly?"结构":"剧本";font.pixelSize:22;font.bold:true;color:t.ink}
                ComboBox {objectName:"screenplaySelector";enabled:!page.structureBusy;Layout.preferredWidth:180;Layout.minimumWidth:100;Layout.maximumWidth:240;Layout.fillWidth:true;font.pixelSize:12;model:page.workspace.screenplays||[];textRole:"title";currentIndex:Math.max(0,(page.workspace.screenplays||[]).findIndex(function(s){return s.id===page.screenplay.id}));onActivated:page.selectScreenplay(model[currentIndex].id)}
                Button {objectName:"screenplayCreate";font.pixelSize:12;leftPadding:4;rightPadding:4;implicitWidth:contentItem.implicitWidth+8;text:"新建剧本";enabled:page.canCreateScreenplay;onClicked:createDialog.open()}
                Button {objectName:"scriptCreativeAgreement";font.pixelSize:12;leftPadding:4;rightPadding:4;implicitWidth:contentItem.implicitWidth+8;text:"剧本创作约定";onClicked:creativeEditor.openFor()}
                Button {objectName:"screenplayDiscuss";font.pixelSize:12;leftPadding:4;rightPadding:4;implicitWidth:contentItem.implicitWidth+8;text:"讨论整部";enabled:!!page.screenplay.id;onClicked:page.openWriting("screenplay",[page.screenplay.id],"")}
                Button {objectName:"screenplayBatchWrite";visible:!page.structureOnly;font.pixelSize:12;leftPadding:4;rightPadding:4;implicitWidth:contentItem.implicitWidth+8;text:"批量写空场";enabled:!!page.screenplay.id;onClicked:page.prepareBatch()}
                Item {Layout.fillWidth:true}
                Button {objectName:"screenplayRefresh";font.pixelSize:12;leftPadding:4;rightPadding:4;implicitWidth:contentItem.implicitWidth+8;text:"刷新";onClicked:{page.load()}}
                Button {objectName:"openStoryboard";visible:!page.structureOnly;text:"进入分镜";enabled:!!page.unit.id;onClicked:{page.captureSelection();app.goTo("storyboard")}}
                Button {objectName:"screenplayCheck";font.pixelSize:12;leftPadding:4;rightPadding:4;implicitWidth:contentItem.implicitWidth+8;text:"检查";enabled:!!page.screenplay.id;onClicked:page.act("check_screenplay",{screenplay_id:page.screenplay.id})}
                Button {objectName:"screenplayExport";font.pixelSize:12;leftPadding:4;rightPadding:4;implicitWidth:contentItem.implicitWidth+8;text:"编译 / 导出";enabled:!!page.screenplay.id;onClicked:{page.exportScreenplayId=page.screenplay.id;page.exportResult=({});page.act("compile_screenplay",{screenplay_id:page.exportScreenplayId,format:"html"})}}
            }
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
        Label {visible:!!page.writingStatus;text:"当前任务："+(page.taskLabels[page.writingStatus]||page.writingStatus)+(page.submittedUnitIds.length?" · 已提交 "+page.submittedUnitIds.length+" 场正文":"");color:page.writingStatus==="failed"?t.warn:t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true;HoverHandler{id:taskHover}ToolTip.visible:taskHover.hovered;ToolTip.text:page.writingTaskId}
        Label { visible:!page.screenplay.id&&!page.storageNeedsReset;text:"创建剧本方案，再从改编时间线导入节点，选择连续范围建立段落。";color:t.muted }
        RowLayout {
            visible:!!page.screenplay.id;Layout.fillWidth:true
            Label {text:page.screenplay.title+" · "+(page.screenplay.format==="series"?"剧集":"单片")+" · 结构修订 "+page.screenplay.structure_revision;color:t.muted;Layout.fillWidth:true;elide:Text.ElideRight}
            Button {visible:page.structureOnly;text:"回剧本工作页";onClicked:app.goTo("screenplay")}
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
                    Flow {
                        Layout.fillWidth:true;spacing:3
                        Button {objectName:"scriptTimelineNew";text:"新建";enabled:!page.timelineBusy&&!app.busy;onClicked:{page.timelineMutationScope={screenplay_id:page.screenplay.id,timeline_id:page.screenplay.timeline_id};timelineEditor.openFor("new","")}}
                        Button {objectName:"scriptTimelineEdit";text:"编辑";enabled:!!range.selectedId&&!page.timelineBusy&&!app.busy;onClicked:{page.timelineMutationScope={screenplay_id:page.screenplay.id,timeline_id:page.screenplay.timeline_id};timelineEditor.openFor("edit",range.selectedId)}}
                        Button {objectName:"scriptTimelineMove";text:"移动";enabled:!!range.selectedId&&!page.timelineBusy&&!app.busy;onClicked:{page.timelineMutationScope={screenplay_id:page.screenplay.id,timeline_id:page.screenplay.timeline_id};timelineEditor.openFor("move",range.selectedId)}}
                        Button {objectName:"scriptTimelineCopy";text:"复制";enabled:!!range.selectedId&&!page.timelineBusy&&!app.busy;onClicked:page.timelineAction("copy_screenplay_timeline_node",range.selectedId)}
                        Button {objectName:"scriptTimelineDelete";text:"删除";enabled:!!range.selectedId&&!page.timelineBusy&&!app.busy;onClicked:{page.timelineDeleteScope={screenplay_id:page.screenplay.id,timeline_id:page.screenplay.timeline_id,node_id:range.selectedId,revision:page.workspace.timeline.revision};timelineDeleteDialog.open()}}
                    }
                    Label {visible:!((page.workspace.timeline||{}).nodes||[]).length;text:"尚无时间点，可新建或从改编导入。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.muted}
                    Label {visible:((page.workspace.timeline||{}).nodes||[]).length>0;text:"当前剧本独立时间线";font.pixelSize:11;Layout.fillWidth:true;color:t.muted}
                ScriptRangePicker {id:range;objectName:"scriptRangePicker";timelineId:page.screenplay.timeline_id || "";Layout.fillWidth:true;Layout.fillHeight:true;nodes:(page.workspace.timeline||{}).nodes || [];t:page.t;onSelectionChanged:page.previewRange()}
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
ColumnLayout {
    visible:!!page.segment.id;Layout.fillWidth:true
    RowLayout {Layout.fillWidth:true;spacing:4
        Button{font.pixelSize:12;leftPadding:4;rightPadding:4;implicitWidth:contentItem.implicitWidth+8;objectName:"scriptWriteSceneTable";text:"讨论 / 拆场";onClicked:page.openWriting("scene_table",[page.segment.id],page.sceneTablePrompt)}
        Button{font.pixelSize:12;leftPadding:4;rightPadding:4;implicitWidth:contentItem.implicitWidth+8;objectName:"scriptAdoptOutline";text:page.segment.outline_state==="adopted"?"取消采用标记":"采用当前场表";onClicked:page.adopt()}
        Button{font.pixelSize:12;leftPadding:4;rightPadding:4;implicitWidth:contentItem.implicitWidth+8;objectName:"scriptAddUnit";text:"新增场次 / 画面段";onClicked:planEditor.openFor(null)}
    }
    Label{text:"采用仅是工作标记，可继续修改";font.pixelSize:11;color:t.muted}
}
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
                            Label{objectName:"scriptSceneOutcome";property string unitId:modelData.id;visible:!!(modelData.plan.outcome||"").trim();text:"结果："+(modelData.plan.outcome||"");wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.body}
                            Flow{Layout.fillWidth:true;spacing:6;Button{text:"编辑场表";onClicked:{page.selectedUnitId=modelData.id;planEditor.openFor(modelData)}}Button{objectName:"scriptOpenBody";text:"打开正文";onClicked:{page.openBody(modelData.id)}}Button{implicitWidth:32;Layout.preferredWidth:32;text:"↑";enabled:index>0;onClicked:page.changeSceneTable({op:"move",unit_id:modelData.id,after_id:index>1?page.units[index-2].id:null})}Button{text:"删除";onClicked:{page.selectedUnitId=modelData.id;deleteUnitDialog.open()}}}
                        }
                    }
                }
                }
                ScriptWorkbenchColumn {
                    id:bodyColumn;objectName:"scriptBodyColumn";namePrefix:"scriptBodyColumn";title:"正文";t:page.t;expandedWidth:400;minimumExpandedWidth:340
                    RowLayout {Layout.fillWidth:true;spacing:4
                        Button {objectName:"scriptWriteCurrentUnit";text:(page.unit.blocks||[]).length?"修改当前场":"写当前场";enabled:!!page.unit.id;onClicked:page.openWriting("unit",[page.unit.id],(page.unit.blocks||[]).length?"":page.effectivePrompts.unit_writing_shortcut||"")}
                        Button {objectName:"scriptAddBlock";text:"添加正文块";enabled:!!page.unit.id;onClicked:blockEditor.openFor(null)}
                    }


                    ComboBox {objectName:"scriptBodyUnit";Layout.fillWidth:true;model:page.units.map(function(u){return {id:u.id,title:page.unitTitle(u)}});textRole:"title";displayText:page.unit.id?page.unitTitle(page.unit):"选择场次";currentIndex:Math.max(0,page.units.findIndex(function(u){return u.id===page.selectedUnitId}));onActivated:page.selectedUnitId=model[currentIndex].id}
                    Label {text:page.unit.id?page.unitTitle(page.unit)+" · "+(page.bodyLabels[page.unit.body_presence]||"正文为空")+" · "+(page.basisLabels[page.unit.basis_state]||"依据未记录"):"";color:t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true;HoverHandler{id:bodyUnitHover}ToolTip.visible:bodyUnitHover.hovered;ToolTip.text:page.unit.id||""}
                    RowLayout {Layout.fillWidth:true;spacing:4
                        Button{objectName:"scriptBlockModify";property string blockId:page.selectedBlockId;text:"修改";enabled:!!page.selectedBlock.id&&!page.blockActionBusy;onClicked:blockEditor.openFor(page.selectedBlock)}
                        Button{objectName:"scriptBlockMoveUp";text:"↑";enabled:page.selectedBlockIndex>0&&!page.blockActionBusy;onClicked:page.moveSelectedBlock()}
                        Button{objectName:"scriptBlockDelete";text:"删除";enabled:!!page.selectedBlock.id&&!page.blockActionBusy;onClicked:page.changeBlock({op:"delete",block_id:page.selectedBlock.id})}
                    }
                    ListView {id:bodyBlocksList;objectName:"scriptBodyBlocks";currentIndex:page.selectedBlockIndex;Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:page.unit.blocks || []
                        delegate:Frame {id:blockCard;objectName:"scriptBlockCard";required property var modelData;required property int index;property string blockId:modelData.id;readonly property bool selected:page.selectedBlockId===modelData.id;width:ListView.view.width
                            height:bodyCard.implicitHeight+24
                            background:Rectangle{color:blockCard.selected?"#e5edff":t.card;border.color:blockCard.selected?t.accent:t.line;radius:4}
                            TapHandler{parent:blockCard;gesturePolicy:TapHandler.DragThreshold;acceptedButtons:Qt.LeftButton;onTapped:page.selectedBlockId=blockCard.modelData.id;onDoubleTapped:{page.selectedBlockId=blockCard.modelData.id;if(!blockEditor.visible)blockEditor.openFor(blockCard.modelData)}}
                            ColumnLayout {id:bodyCard;anchors.fill:parent
                                Label{text:(page.blockLabels[modelData.type]||"正文块")+" "+(index+1);color:t.muted;font.pixelSize:10;HoverHandler{id:blockHover}ToolTip.visible:blockHover.hovered;ToolTip.text:modelData.id}
                                Label{visible:modelData.type==="dialogue";text:(modelData.payload.speaker_label||"未命名发声者")+" · "+({on_screen:"现场",off_screen:"画外",voice_over:"旁白",inner_voice:"内心声"}[modelData.payload.delivery]||"");color:t.ink;font.bold:true}
                                TextArea {objectName:"scriptBlockDisplayText";property string blockId:modelData.id;onActiveFocusChanged:if(activeFocus)page.selectedBlockId=modelData.id;text:modelData.payload.text || modelData.payload.kind || "";readOnly:true;selectByMouse:true;wrapMode:TextEdit.Wrap;Layout.fillWidth:true;background:null
                                    id:blockText
                                    Component.onCompleted:bridge.observeBlockTextDoubleClicks(blockText)
                                    Connections{target:bridge;function onBlockTextDoubleClicked(blockId){if(blockId===blockText.blockId&&page.visible&&blockCard.visible&&!page.blockActionBusy){page.selectedBlockId=modelData.id;if(!blockEditor.visible)blockEditor.openFor(modelData)}}}
                                }
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
    ScriptTimelineEditor {id:timelineEditor;host:page}
    Dialog {
        id:timelineDeleteDialog;objectName:"scriptTimelineDeleteDialog";title:"删除时间点及子树";modal:true;anchors.centerIn:parent;width:Math.min(440,page.width-30)
        contentItem:ColumnLayout {
            Label {Layout.fillWidth:true;wrapMode:Text.Wrap;text:"将永久移除本剧本所选时间点、全部下属和关联状态。已有段落、场表和正文保留，已绑定范围可能失效并显示诊断。";color:t.warn}
            RowLayout {Layout.fillWidth:true;Item{Layout.fillWidth:true}Button{text:"取消";onClicked:timelineDeleteDialog.close()}Button{objectName:"scriptTimelineDeleteConfirm";text:"删除节点及子树";enabled:!page.timelineBusy&&!app.busy;onClicked:{var scope=page.timelineDeleteScope;if(scope.screenplay_id!==page.screenplay.id||scope.timeline_id!==page.screenplay.timeline_id){timelineDeleteDialog.close();return}page.timelineAction("delete_screenplay_timeline_node",scope.node_id,scope.revision)}}}
        }
    }
    Dialog {
        id:timelineImportDialog;objectName:"scriptTimelineImportDialog";title:page.timelineImportScope.appending?"追加导入时间点":"从改编时间线导入独立副本";modal:true;anchors.centerIn:parent;width:Math.min(1180,page.width-40);height:Math.min(620,page.height-30)
        contentItem:ColumnLayout {
            Label {text:page.timelineImportScope.appending?"新时间点将追加到剧本时间表；已有节点、段落、场表和正文保留。可选择已导入的容器，追加其新增子项。":"选择时间点及其子项，所需人物、地点和属性一并导入。剧本保留独立副本，源改编后续变化不会自动覆盖。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.body}
            TimelineImportColumns {
                objectName:"scriptTimelineImportPicker"
                Layout.fillWidth:true;Layout.fillHeight:true
                t:page.t
                candidates:page.timelineImportCandidates
                selection:page.timelineImportSelection
                onSelectionEdited:function(ids){page.timelineImportSelection=ids}
            }
            RowLayout {Layout.fillWidth:true;Label{text:"已选 "+page.timelineImportSelection.length+" 项";color:t.muted}Item{Layout.fillWidth:true}Button{text:"取消";onClicked:timelineImportDialog.close()}Button{objectName:"scriptTimelineImportConfirm";text:page.timelineImportScope.appending?"追加所选":"导入所选";enabled:page.timelineImportSelection.length>0;onClicked:page.importTimelineSelection()}}
        }
    }
    Dialog {
        id:createDialog;
        SubmissionState{id:createSubmission;owner:createDialog;app:page.app;controller:page;operation:"create_screenplay";onRestoreRequested:function(a){createTitle.text=a.title||"";createFormat.currentIndex=a.format==="series"?1:0}}
        objectName:"scriptCreateDialog";title:"新建剧本方案";modal:true;anchors.centerIn:parent;width:420
        contentItem:ColumnLayout {SubmissionFeedback{state:createSubmission}TextField{id:createTitle;objectName:"scriptCreateTitle";placeholderText:"剧本名称";Layout.fillWidth:true}ComboBox{id:createFormat;model:["单片","剧集"]}Button{objectName:"scriptCreateSave";text:"创建";enabled:!!createTitle.text.trim()&&page.canCreateScreenplay;onClicked:page.act("create_screenplay",{title:createTitle.text,format:createFormat.currentIndex===1?"series":"single",reason:"创建剧本方案"})}}
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
            RowLayout {
                Layout.fillWidth:true
                Label {objectName:"scriptWritingAgreementSummary";Layout.fillWidth:true;wrapMode:Text.Wrap;color:t.muted;text:"本次写作使用目标剧本的有效创作约定；本次具体要求可进一步说明。"}
                Button {objectName:"scriptWritingAgreementOpen";text:"查看 / 编辑剧本创作约定";onClicked:creativeEditor.openFor(page.writingTarget.screenplay_id)}
            }
            TextArea{id:writingPrompt;objectName:"scriptWritingPrompt";placeholderText:"写作或修改要求";wrapMode:TextEdit.Wrap;Layout.fillWidth:true;Layout.fillHeight:true}
            RowLayout{Item{Layout.fillWidth:true}Button{text:"取消";onClicked:writingDialog.close()}Button{objectName:"scriptWritingSend";text:"发送到当前聊天";enabled:!!writingPrompt.text.trim()&&(page.writingTarget.ids||[]).length>0;onClicked:{app.assistantOpen=true;bridge.screenplayWritingTurn(writingPrompt.text,page.writingTarget)}}}
        }
    }
    Dialog {
        id:exportDialog;objectName:"scriptExportDialog";title:"剧本排版预览与输出";modal:true;anchors.centerIn:parent;width:Math.min(980,page.width-32);height:Math.min(760,page.height-24)
        contentItem:ColumnLayout {
            RowLayout {
                Layout.fillWidth:true
                Label{text:"保存格式"}
                ComboBox {objectName:"scriptExportFormat";model:page.exportFormats;textRole:"label";currentIndex:Math.max(0,page.exportFormats.findIndex(function(row){return row.id===page.exportFormat}));onActivated:page.exportFormat=model[currentIndex].id}
                Button {objectName:"scriptCompileSettingsOpen";text:"排版设置";onClicked:compileSettings.openSettings()}
                Button {objectName:"scriptExportSave";text:"保存文件…";enabled:page.exportScreenplayId===page.screenplay.id;onClicked:exportFile.open()}
                Item{Layout.fillWidth:true}
                Button{text:"关闭";onClicked:exportDialog.close()}
            }
            Label{text:"以下为剧本排版预览。保存时按所选格式输出；检查提示不会写入正文。";color:t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
            Label{visible:(page.exportResult.diagnostics||[]).length>0;text:page.diagnosticsText(page.exportResult.diagnostics);color:t.warn;wrapMode:Text.Wrap;Layout.fillWidth:true}
            ScrollView {
                id: exportPreviewScroll
                Layout.fillWidth:true;Layout.fillHeight:true;clip:true
                contentWidth:Math.max(availableWidth,210*96/25.4)
                contentHeight:previewText.height
                background:Rectangle{color:"#e6e7e9"}
                Item {
                    width:exportPreviewScroll.contentWidth;height:previewText.height
                    TextArea {
                        id:previewText;objectName:"scriptExportText"
                        readonly property var pageMargins:((page.exportResult.compile_settings||{}).settings||{}).page_margins_mm||({top:25,right:23,bottom:25,left:23})
                        width:210*96/25.4;anchors.horizontalCenter:parent.horizontalCenter
                        height:Math.max(297*96/25.4,contentHeight+topPadding+bottomPadding)
                        topPadding:pageMargins.top*96/25.4;rightPadding:pageMargins.right*96/25.4
                        bottomPadding:pageMargins.bottom*96/25.4;leftPadding:pageMargins.left*96/25.4
                        text:page.exportResult.qt_preview_html||page.exportResult.preview_html||""
                        textFormat:TextEdit.RichText;readOnly:true;selectByMouse:true;wrapMode:TextEdit.Wrap;color:"#181818"
                        background:Rectangle{color:"white"}
                    }
                }
            }
        }
    }
    FileDialog {
        id:exportFile;objectName:"scriptExportFile";title:"保存已编译剧本";fileMode:FileDialog.SaveFile
        defaultSuffix:page.exportChoice.extension;nameFilters:[page.exportChoice.filter]
        onAccepted:bridge.saveScreenplayExport(page.exportScreenplayId,page.exportFormat,selectedFile.toString())
    }
    Dialog{id:formatDialog;objectName:"scriptFormatDialog";title:"完整重组结构";modal:true;anchors.centerIn:parent;width:Math.min(460,parent.width-24);standardButtons:Dialog.Ok|Dialog.Cancel;contentItem:Label{text:page.screenplay.format==="series"?"将各集下的幕和段落搬到单片根级，并删除空集。":"创建第1集，并将当前根级幕和段落全部归入此集。";wrapMode:Text.Wrap}onAccepted:page.changeFormat()}
    CompileSettingsEditor{id:compileSettings;host:page}
    ScriptStructureEditor{id:structureDialog;host:page}
    ScriptSegmentEditor{id:segmentDialog;host:page}
    CreativeAgreementEditor{id:creativeEditor;host:page}
}





