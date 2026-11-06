import QtQuick

QtObject {
    property var host
    function loadBrowse(spec) { bridge.loadAdaptationBrowse(spec) }
    function nodeDetail(id) { bridge.loadAdaptationDetail(id, host.cutEventId) }
    function loadNodeProperties(id) { bridge.loadAdaptationProperties(id, host.cutEventId) }
    function loadNodeEditor(id) { bridge.loadAdaptationEditor(id, host.cutEventId) }
    function loadMaterial(id) { bridge.loadAdaptationMaterial(id, host.cutEventId) }
    function change(action, args) { args.expected_revision = host.revision; bridge.changeAdaptation(action, args) }
    function reviseNode(id, fields) { change("edit", {node_id: id, changes: fields}) }
    function propertyArgs(fields){var args={node_id:fields.node_id,property_id:fields.property_id,event_id:fields.event_id,op:fields.op};if(["set","add","remove"].indexOf(fields.op)>=0)args.value=fields.value;["scope","holder_id","context_id","reason"].forEach(function(key){if(fields[key]!==undefined&&fields[key]!==null)args[key]=fields[key]});return args}
    function addPropertyRecord(fields) { change("property", propertyArgs(fields)) }
    function correctPropertyRecord(id, fields) { var args=propertyArgs(fields);args.record_id=id;change("property",args) }
    function unavailable() { host.app.toast("请在资产页面管理改编视觉设定；此项提取数据操作不适用于改编", true) }
    function deletePropertyRecords(ids) { change("delete_property_changes",{record_ids:JSON.parse(ids)}) }
    function renameProperty(a,b) { unavailable() }
    function categorizeProperties(a,b) { unavailable() }
    function mergeProperties(a,b) { unavailable() }
    function savePropertyCategories(a) { unavailable() }
    function openAssets() { host.app.goTo("assets") }
    function addMaterialCard() { openAssets() }
    function updateMaterialCard() { openAssets() }
    function addMaterialImage() { openAssets() }
    function removeMaterialCard() { openAssets() }
    function removeMaterialImage() { openAssets() }
    function updateMaterialImageCaption() { openAssets() }
    function extractEventActions() { unavailable() }
}


