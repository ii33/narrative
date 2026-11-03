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
    function addPropertyRecord(fields) { change("property", {node_id: fields.node_id, property_id: fields.property_id, value: fields.value, event_id: fields.event_id || null}) }
    function correctPropertyRecord(id, fields) { change("property", {node_id: fields.node_id, property_id: fields.property_id, value: fields.value, event_id: fields.event_id || null, record_id: id}) }
    function expirePropertyRecords(ids, reason) { bridge.expireAdaptationProperties(JSON.parse(ids), host.cutEventId, host.revision, reason) }
    function unavailable() { host.app.toast("请在资产页面管理改编视觉设定；此项提取数据操作不适用于改编", true) }
    function deletePropertyRecords(ids) { unavailable() }
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


