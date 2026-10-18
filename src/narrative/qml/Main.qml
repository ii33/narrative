import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Fusion as Fusion
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: root
    objectName: "nativeRoot"
    width: 1500
    height: 940
    minimumWidth: 1120
    minimumHeight: 720
    visible: true
    title: projectInfo.title ? projectInfo.title + " · 叙事图工作台" : "叙事图工作台"
    color: palette.window

    readonly property color ink: "#172b4d"
    readonly property color muted: "#60738d"
    readonly property color line: "#dce4ef"
    readonly property color paper: "#f5f8fc"
    readonly property color white: "#ffffff"
    readonly property color accent: "#2859c5"
    readonly property color accentSoft: "#e9f0ff"
    readonly property color green: "#167d62"
    readonly property color red: "#b43d42"
    property bool busy: false
    property string errorText: ""
    property string currentPage: "home"
    property var projectInfo: ({})
    property var sourceItems: []
    property var candidateItems: []
    property var runItems: []
    property var currentTrace: ({})
    property var currentCandidate: ({})
    property var currentDetail: ({})
    property var currentSource: ({})
    property var currentResponse: ({})
    property var providerProfile: ({})
    property var providerCatalog: []
    property bool showServiceSettings: false
    property string profileNotice: ""
    property bool profileSaveInFlight: false
    property string selectedNodeId: ""
    property string selectedCandidateId: ""
    property string selectedRunId: ""
    property string selectedSourceId: ""
    property string selectedSearchType: "全部类型"
    property string graphNotice: ""
    property string candidateFilter: "全部"
    property string taskText: "梳理人物、事件、关系与有证据支持的时间线。"
    property var selectedSourceIds: []
    property var projectItems: []
    property var nodePositions: ({})
    property int nodeLayoutRevision: 0
    property int graphRevision: 0
    property bool graphTruncated: false
    property string graphMode: "hierarchy"
    property var timelineData: ({})
    property var timelinePath: []
    property var timelineSelections: ({})
    property string queryFocusId: ""
    property string queryFocusName: ""
    property int queryDistance: 2
    property string queryDirection: "both"
    property var queryNodeTypes: null
    property var queryRelations: null
    property var queryTable: ({})
    property var queryPaths: []
    property string queryJson: ""
    property bool unifiedQueryActive: false
    readonly property var nodeTypeOptions: ["person","group","item","location","event","scene","storyline","other"]
    readonly property var relationOptions: ["containment","participation","temporal","relation","attribute"]
    function querySpec() {
        return {focus_id: queryFocusId || null, depth: queryDistance, direction: queryDirection,
            node_types: queryNodeTypes, relation_semantics: queryRelations, limit: 2000,
            disclosure_cut: readingCut.source_id ? readingCut : null}
    }
    function runGraphQuery() {
        unifiedQueryActive = true
        timelineData = ({}); timelinePath = []; timelineSelections = ({})
        graphNodesModel.clear(); graphEdgesModel.clear(); queryTable = ({})
        currentHierarchy = ({}); rebuildHierarchy(false)
        currentDetail = ({}); selectedNodeId = ""
        bridge.loadGraphView(querySpec())
    }
    function setQueryFocus(identity, name) {
        queryFocusId = identity || ""; queryFocusName = name || identity || ""
        hierarchyFocusId = ""
        runGraphQuery()
    }
    function toggleQueryFilter(field, key, checked) {
        var all = field === "types" ? nodeTypeOptions : relationOptions
        var current = field === "types" ? queryNodeTypes : queryRelations
        var next = current === null ? all.slice() : current.slice()
        var index = next.indexOf(key)
        if (checked && index < 0) next.push(key)
        if (!checked && index >= 0) next.splice(index, 1)
        if (field === "types") queryNodeTypes = next
        else queryRelations = next
        runGraphQuery()
    }
    function resetQueryFilters() {
        queryNodeTypes = null; queryRelations = null; queryDistance = 2; queryDirection = "both"
        displayType = "all"; displayRelation = "all"; displayDepth = -1
        runGraphQuery()
    }
    function relationName(value) {
        return ({containment:"组织",participation:"参与",temporal:"时序",relation:"一般关系",attribute:"属性"})[value] || value
    }
    function tableRelations(row) { return (row.relations || row.attributes || []).map(function(a) { return a.label || a.predicate_label || a.predicate_id }).join("、") }
    function tableEvidenceCount(row) { return row.evidence_count === undefined ? (row.attributes || []).reduce(function(count, a) { return count + (a.evidence || []).length }, 0) : row.evidence_count }
    function queryNodeName(identity) {
        var rows = queryTable.rows || []
        for (var i=0; i<rows.length; ++i) if (rows[i].id === identity) return rows[i].name
        return "关联节点"
    }
    function centerQueryFocus() {
        var node = nodeById(queryFocusId)
        if (!node) { fitGraph(); return }
        var cx = node.x + 95, cy = node.y + 46, extentX = 190, extentY = 92
        for (var i=0; i<graphNodesModel.count; ++i) {
            var other = graphNodesModel.get(i)
            extentX = Math.max(extentX, Math.abs(other.nodeX-cx), Math.abs(other.nodeX+190-cx))
            extentY = Math.max(extentY, Math.abs(other.nodeY-cy), Math.abs(other.nodeY+92-cy))
        }
        graphScene.scale = Math.max(0.35, Math.min(1, (viewport.width/2-30)/extentX, (viewport.height/2-70)/extentY))
        graphScene.x = viewport.width / 2 - (node.x + 95) * graphScene.scale
        graphScene.y = viewport.height / 2 - (node.y + 46) * graphScene.scale
    }
    onTimelinePathChanged: Qt.callLater(function() { if (timelineScroll.contentItem) timelineScroll.contentItem.contentY = 0 })
    function timelineLayer(identity) {
        var layers = (timelineData.nested_axes || {}).layers || []
        for (var i=0; i<layers.length; ++i) if (layers[i].id === identity) return layers[i]
        return ({id:identity,events:[]})
    }
    function timelineLayers() {
        var layers = [timelineLayer((timelineData.nested_axes || {}).root_axis_id || "root")]
        for (var i=0; i<timelinePath.length; ++i) {
            var event = (layers[i].events || []).find(function(item) { return item.id === root.timelinePath[i] })
            if (!event || !event.child_axis_id) break
            layers.push(timelineLayer(event.child_axis_id))
        }
        return layers
    }
    function upperTimelineLayers() { return timelineLayers().slice(1).reverse() }
    function closeTimelineLayer(level) {
        timelinePath = timelinePath.slice(0, Math.max(0,level-1))
        var kept = ({}), layers = timelineLayers()
        for (var i=0; i<layers.length; ++i) if (timelineSelections[layers[i].id]) kept[layers[i].id] = timelineSelections[layers[i].id]
        timelineSelections = kept
    }
    function clickTimelineEvent(axisId, identity) {
        var layers = timelineLayers(), level = layers.findIndex(function(layer) { return layer.id === axisId })
        if (level < 0) return
        var event = (layers[level].events || []).find(function(item) { return item.id === identity })
        if (!event) return
        selectNode(identity)
        var path = timelinePath.slice(0,level)
        if (timelinePath[level] !== identity && event.child_axis_id) path.push(identity)
        timelinePath = path
        var selections = ({})
        for (var i=0; i<level; ++i) selections[layers[i].id] = timelineSelections[layers[i].id]
        selections[axisId] = identity
        timelineSelections = selections
    }
    function validateTimelinePath() {
        var path = [], selections = ({}), layer = timelineLayer("root")
        for (var i=0; i<timelinePath.length; ++i) {
            var event = (layer.events || []).find(function(item) { return item.id === root.timelinePath[i] })
            if (!event || !event.child_axis_id) break
            path.push(event.id); selections[layer.id] = event.id
            layer = timelineLayer(event.child_axis_id)
        }
        if ((layer.events || []).some(function(event) { return event.id === root.timelineSelections[layer.id] })) selections[layer.id] = timelineSelections[layer.id]
        timelinePath = path
        timelineSelections = selections
    }
    property string hierarchyFocusId: ""
    property var readingCut: ({})
    property var readingPositions: [{label: "全部已审核内容", source_id: "", start: 0, excerpt: ""}]
    property var readingIndex: ({})
    property string readingNotice: ""
    function chooseReadingPosition(index) {
        readingProgress.currentIndex = index
        var position = readingPositions[index]
        readingCut = position.source_id ? {source_id: position.source_id, start: position.start} : ({})
        currentDetail = ({})
        selectedNodeId = ""
        searchResultsModel.clear()
        graphNodesModel.clear()
        graphEdgesModel.clear()
        currentHierarchy = ({})
        hierarchyRowsModel.clear()
        hierarchySharedRowsModel.clear()
        expandedRelationIds = ({})
        relationRequestPending = false
        timelineData = ({})
        timelinePath = []
        timelineSelections = ({})
        hierarchyFocusId = ""
        runGraphQuery()
        search()
    }
    function selectedEventHistory() {
        var events = readingIndex.events || []
        for (var i = 0; i < events.length; ++i) {
            if (events[i].id !== selectedNodeId) continue
            return (events[i].history || []).map(function(row) {
                var parent = hierarchyNode(row.subject_id, false)
                return (parent ? parent.name : "归属未明") + (row.assessment === "retracted" ? "（已撤回）" : "")
            }).join(" → ")
        }
        return ""
    }
    function loadReadingView(focus) {
        hierarchyFocusId = focus
        if (unifiedQueryActive && JSON.stringify(timelineData.disclosure_cut || {}) === JSON.stringify(readingCut)) {
            expandedHierarchyIds = ({})
            rebuildHierarchy(false)
            return
        }
        bridge.loadReadingHierarchy(focus, readingCut)
    }
    function focusParent() {
        var node = hierarchyNode(hierarchyFocusId, false)
        loadReadingView(node && node.parent_id ? node.parent_id : "")
    }
    property bool showTemporalDetails: false
    function axisVisiblePoints(axis) {
        if (displayRelation !== "all" && displayRelation !== "temporal") return []
        return (axis.points || []).filter(function(point) { return root.nodeShown(point.event_id, "event") }).slice().sort(function(a,b) {
            if (a.ordinal !== b.ordinal) return a.ordinal-b.ordinal
            var order = {start:0, point:1, end:2}
            return (order[a.boundary] || 0) - (order[b.boundary] || 0)
        })
    }
    property string displayType: "all"
    property string displayRelation: "all"
    property int displayDepth: -1
    function nodeShown(identity, kind) {
        if (displayType !== "all" && kind !== displayType) return false
        var h = hierarchyNode(identity, false)
        if (!h) {
            var events = timelineData.events || []
            for (var i = 0; i < events.length; ++i) if (events[i].id === identity) { h = events[i]; break }
        }
        var eventDepth = 0
        var ancestor = h
        var seen = ({})
        while (ancestor && ancestor.id !== hierarchyFocusId && ancestor.parent_id && !seen[ancestor.parent_id]) {
            seen[ancestor.parent_id] = true
            ancestor = hierarchyNode(ancestor.parent_id, false)
            if (ancestor && ancestor.node_type === "event") eventDepth++
        }
        return !(displayDepth >= 0 && kind === "event" && eventDepth > displayDepth)
    }
    function visibleNodeCount() {
        var count = 0
        var revision = nodeLayoutRevision
        for (var i = 0; i < graphNodesModel.count; ++i) {
            var n = graphNodesModel.get(i)
            if (nodeShown(n.id, n.node_type)) count++
        }
        return count
    }
    onDisplayTypeChanged: rebuildHierarchy(false)
    onDisplayDepthChanged: rebuildHierarchy(false)
    function timelineVisibleCount() {
        var events = timelineData.events || []
        var count = 0
        for (var i = 0; i < events.length; ++i) if (nodeShown(events[i].id, "event")) count++
        return count
    }
    function boundaryLabel(value) {
        if (value && typeof value === "object") {
            if (value.form === "number") return String(value.number)
            if (value.form === "datetime") return String(value.datetime)
            value = value.boundary || value.form
        }
        return ({start:"开始",end:"结束",point:"时点",whole:"整体"})[value] || value || "整体"
    }
    function eventName(identity) {
        var events = timelineData.events || []
        for (var i = 0; i < events.length; ++i) if (events[i].id === identity) return events[i].name
        return "事件"
    }
    function coordinate(ref) { return ref.number !== undefined ? Number(ref.number) : Date.parse(ref.datetime || ref.value) }
    function laneRange(lane) {
        var values = []
        for (var i = 0; i < lane.events.length; ++i)
            for (var j = 0; j < lane.events[i].coordinate_refs.length; ++j) {
                var ref = lane.events[i].coordinate_refs[j]
                if (ref.timeline_id === lane.timeline_id && ref.form === lane.form) values.push(coordinate(ref))
            }
        var low = Math.min.apply(null, values), high = Math.max.apply(null, values)
        return {low:low, high:high, span:Math.max(1,high-low)}
    }
    function laneTicks(lane) {
        var range = laneRange(lane)
        if (range.high === range.low) return [range.low]
        return [range.low, range.low + range.span/4, range.low + range.span/2, range.low + range.span*3/4, range.high]
    }
    function coordinateText(value, form) { return form === "number" ? String(Math.round(value*1000)/1000) : new Date(value).toISOString().slice(0,16).replace("T"," ") }
    function timePosition(ref, lane, width) {
        var range = laneRange(lane)
        return 20 + (coordinate(ref) - range.low) / range.span * Math.max(20,width-40)
    }
    function timeLabel(event) {
        var refs = (event.coordinate_refs || []).map(function(ref) { return String(ref.number !== undefined ? ref.number : (ref.datetime || ref.value || "未知")) }).join(" — ")
        if (event.occurrence && event.occurrence.kind === "interval") {
            if (event.occurrence.start.state !== "known") refs = "开始未明 — " + refs
            if (event.occurrence.end.state !== "known") refs += " — 结束未明"
        }
        return refs
    }
    function temporalLabel(relation) {
        return ({before:"早于", after:"晚于", same:"同时", same_time:"同时", simultaneous:"同时", overlaps:"时间重叠", contains:"时间包含", timecontains:"时间包含", time_contains:"时间包含"})[relation] || relation
    }

    property var currentHierarchy: ({})
    property var candidateHierarchy: ({})
    property var expandedRelationIds: ({})
    property bool relationRequestPending: false
    property var expandedHierarchyIds: ({})
    property var expandedCandidateIds: ({})
    property bool showSharedRoots: false
    property bool showCandidateSharedRoots: false

    ListModel { id: graphNodesModel }
    ListModel { id: hierarchyRowsModel }
    ListModel { id: hierarchySharedRowsModel }
    ListModel { id: candidateHierarchyRowsModel }
    ListModel { id: candidateSharedRowsModel }
    ListModel { id: graphEdgesModel }
    ListModel { id: searchResultsModel }
    ListModel { id: candidateListModel }
    ListModel { id: candidateRowsModel }
    ListModel { id: sourceRowsModel }
    ListModel { id: runRowsModel }
    ListModel { id: projectRowsModel }

    function openProjectPath(path) {
        errorText = ""
        bridge.openProject(path)
    }

    function selectNode(identity) {
        selectedNodeId = identity
        currentDetail = ({})
        bridge.nodeDetail(identity)
    }

    function hierarchyNode(identity, candidate) {
        var nodes = (candidate ? candidateHierarchy : currentHierarchy).nodes || []
        for (var i = 0; i < nodes.length; ++i)
            if (nodes[i].id === identity) return nodes[i]
        return null
    }

    function hierarchyPath(identity) {
        var names = [], seen = ({}), node = hierarchyNode(identity, false)
        while (node && node.parent_id && !seen[node.parent_id]) {
            seen[node.parent_id] = true
            node = hierarchyNode(node.parent_id, false)
            if (node) names.unshift(node.name || "未命名节点")
        }
        return names.join(" › ")
    }

    function orderedRelatedNodes() {
        var related = (currentDetail.related_nodes || []).slice()
        var hierarchy = currentHierarchy.nodes || []
        var placement = ({})
        for (var i = 0; i < hierarchy.length; ++i)
            placement[hierarchy[i].id] = {depth: hierarchy[i].depth, order: i}
        related.sort(function(a, b) {
            var aEvent = a.node_type === "event" || a.node_type === "scene" || a.node_type === "storyline"
            var bEvent = b.node_type === "event" || b.node_type === "scene" || b.node_type === "storyline"
            if (aEvent !== bEvent) return aEvent ? -1 : 1
            var pa = placement[a.id] || {depth: 999, order: 999999}
            var pb = placement[b.id] || {depth: 999, order: 999999}
            if (pa.depth !== pb.depth) return pa.depth - pb.depth
            return pa.order - pb.order
        })
        return related
    }

    function rebuildHierarchy(candidate) {
        var view = candidate ? candidateHierarchy : currentHierarchy
        var expanded = candidate ? expandedCandidateIds : expandedHierarchyIds
        var model = candidate ? candidateHierarchyRowsModel : hierarchyRowsModel
        var sharedModel = candidate ? candidateSharedRowsModel : hierarchySharedRowsModel
        model.clear()
        sharedModel.clear()
        var seen = ({})
        function visit(identity, depth, destination) {
            if (seen[identity]) return
            var node = root.hierarchyNode(identity, candidate)
            if (!node) return
            seen[identity] = true
            var children = node.child_ids || []
            destination.append({node_id: identity, name: node.name || "未命名节点",
                node_type: node.node_type || "other", depth: depth,
                child_count: children.length, expanded: Boolean(expanded[identity]),
                summary: node.identity_note || node.summary || ""})
            if (expanded[identity] || displayType !== "all" || displayDepth >= 0)
                for (var c = 0; c < children.length; ++c) visit(children[c], depth + 1, destination)
        }
        var roots = !candidate && hierarchyFocusId ? [hierarchyFocusId] : (view.roots || [])
        var priority = {storyline:0, scene:1, event:2, group:3, location:4, person:5, item:6, other:7}
        roots = roots.slice().sort(function(a, b) {
            var na = root.hierarchyNode(a, candidate), nb = root.hierarchyNode(b, candidate)
            return priority[na ? na.node_type : "other"] - priority[nb ? nb.node_type : "other"]
        })
        for (var r = 0; r < roots.length; ++r) {
            var rootNode = hierarchyNode(roots[r], candidate)
            var primary = rootNode && ["storyline", "scene", "event"].indexOf(rootNode.node_type) >= 0
            visit(roots[r], 0, primary ? model : sharedModel)
        }
    }

    function toggleHierarchy(identity, candidate) {
        var current = candidate ? expandedCandidateIds : expandedHierarchyIds
        var next = Object.assign({}, current)
        if (next[identity]) delete next[identity]
        else next[identity] = true
        if (candidate) expandedCandidateIds = next
        else expandedHierarchyIds = next
        rebuildHierarchy(candidate)
    }

    function hierarchyVisibleIds(candidate) {
        var model = candidate ? candidateHierarchyRowsModel : hierarchyRowsModel
        var ids = []
        for (var i = 0; i < model.count; ++i) ids.push(model.get(i).node_id)
        return ids
    }

    function toggleRelationNode(identity) {
        if (expandedRelationIds[identity]) collapseNode(identity)
        else expandNode(identity)
    }

    function collapseNode(identity) {
        if (relationRequestPending) return
        relationRequestPending = true
        bridge.collapseNode(identity)
    }

    function expandNode(identity) {
        if (relationRequestPending) return
        relationRequestPending = true
        selectedNodeId = identity
        graphNotice = "正在展开一跳直接关系…"
        bridge.expandNode(identity)
    }

    function fitGraph() {
        if (viewport.width <= 0 || viewport.height <= 0)
            return
        if (graphNodesModel.count === 0) {
            graphScene.scale = 0.8
            graphScene.x = viewport.width / 2 - 2000 * graphScene.scale
            graphScene.y = viewport.height / 2 - 1500 * graphScene.scale
            return
        }
        var minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9
        for (var i = 0; i < graphNodesModel.count; ++i) {
            var n = graphNodesModel.get(i)
            minX = Math.min(minX, n.nodeX); minY = Math.min(minY, n.nodeY)
            maxX = Math.max(maxX, n.nodeX + 190); maxY = Math.max(maxY, n.nodeY + 92)
        }
        var contentW = Math.max(560, maxX - minX + 300)
        var contentH = Math.max(420, maxY - minY + 260)
        graphScene.scale = Math.max(0.35, Math.min(1.25, Math.min(viewport.width / contentW, viewport.height / contentH)))
        graphScene.x = (viewport.width - (maxX + minX) * graphScene.scale) / 2
        graphScene.y = (viewport.height - (maxY + minY) * graphScene.scale) / 2
    }

    function nodeIndex(identity) {
        for (var i = 0; i < graphNodesModel.count; ++i)
            if (graphNodesModel.get(i).id === identity) return i
        return -1
    }

    function nodeById(identity) {
        var layoutVersion = nodeLayoutRevision
        var i = nodeIndex(identity)
        if (i < 0) return null
        var row = graphNodesModel.get(i)
        return {id: row.id, name: row.name, x: row.nodeX, y: row.nodeY, node_type: row.node_type, layoutRevision: layoutVersion}
    }

    function edgeExists(identity) {
        for (var i = 0; i < graphEdgesModel.count; ++i)
            if (graphEdgesModel.get(i).assertion_id === identity) return true
        return false
    }

    function initialPosition(index, total) {
        if (unifiedQueryActive && queryFocusId) {
            var focus = nodeById(queryFocusId)
            var centerX = focus ? focus.x : 1905, centerY = focus ? focus.y : 1454
            if (index === 0) return {x: centerX, y: centerY}
            var theta = index * 2.399963229728653, distance = 180 + Math.sqrt(index) * 80
            return {x: centerX + Math.cos(theta) * distance, y: centerY + Math.sin(theta) * distance * 0.68}
        }
        var angle = index * 2.399963229728653
        var radius = 90 + Math.sqrt(index + 1) * 145
        return { x: 2000 + Math.cos(angle) * radius - 95,
                 y: 1500 + Math.sin(angle) * radius * 0.68 - 46 }
    }

    function incrementalPosition(index, total, centerId) {
        var center = nodeById(centerId)
        if (!center) return initialPosition(graphNodesModel.count + index, total)
        var angle = index * 2.399963229728653
        var radius = 230 + Math.sqrt(index + 1) * 72
        return { x: center.x + 95 + Math.cos(angle) * radius - 95,
                 y: center.y + 46 + Math.sin(angle) * radius - 46 }
    }

    function setCanvas(payload, reset) {
        graphRevision = payload.revision || graphRevision
        if (reset) {
            expandedRelationIds = ({})
            relationRequestPending = false
            graphNodesModel.clear()
            graphEdgesModel.clear()
            var newNodes = payload.nodes || []
            for (var i = 0; i < newNodes.length; ++i) {
                var item = newNodes[i]
                var pos = (item.x !== undefined && item.y !== undefined)
                        ? {x: item.x, y: item.y} : initialPosition(i, newNodes.length)
                graphNodesModel.append({id: item.id, name: item.name || "未命名节点",
                    node_type: item.node_type || "other", kind_label: item.kind_label || "节点",
                    summary: item.summary || "", nodeX: pos.x, nodeY: pos.y})
            }
            var nextPositions = ({})
            for (var j = 0; j < graphNodesModel.count; ++j) {
                var row = graphNodesModel.get(j)
                nextPositions[row.id] = {x: row.nodeX, y: row.nodeY}
            }
            nodePositions = nextPositions
            graphEdgesModel.clear()
            var resetEdges = payload.edges || []
            for (var k = 0; k < resetEdges.length; ++k)
                if (!edgeExists(resetEdges[k].assertion_id)) graphEdgesModel.append(resetEdges[k])
            graphTruncated = Boolean(payload.truncated)
            nodeLayoutRevision += 1
            graphNotice = payload.pending_excluded ? "画布只显示正式图的直接断言；待审核批次不会混入。" : ""
            Qt.callLater(fitGraph)
        } else {
            var added = payload.nodes_added || []
            for (var m = 0; m < added.length; ++m) {
                var newNode = added[m]
                if (nodeIndex(newNode.id) < 0) {
                    var where = (newNode.x !== undefined && newNode.y !== undefined)
                            ? {x: newNode.x, y: newNode.y}
                            : incrementalPosition(m, added.length, payload.center_id)
                    graphNodesModel.append({id: newNode.id, name: newNode.name || "未命名节点",
                        node_type: newNode.node_type || "other", kind_label: newNode.kind_label || "节点",
                        summary: newNode.summary || "", nodeX: where.x, nodeY: where.y})
                }
            }
            var addedEdges = payload.edges_added || []
            for (var n = 0; n < addedEdges.length; ++n)
                if (!edgeExists(addedEdges[n].assertion_id)) graphEdgesModel.append(addedEdges[n])
            graphTruncated = Boolean(payload.truncated)
            nodeLayoutRevision += 1
            graphNotice = payload.already_expanded
                    ? "该节点已展开；画布已去重，不会重复添加边。"
                    : (added.length ? "新增 " + added.length + " 个节点、" + addedEdges.length + " 条直接断言边。" : "没有新的可见邻接节点。")
        }
    }

    function updateNodePosition(identity, x, y) {
        var idx = nodeIndex(identity)
        if (idx < 0) return
        graphNodesModel.setProperty(idx, "nodeX", x)
        graphNodesModel.setProperty(idx, "nodeY", y)
        nodeLayoutRevision += 1
        var copy = Object.assign({}, nodePositions)
        copy[identity] = {x: x, y: y}
        nodePositions = copy
    }

    function previewNodePosition(identity, x, y) {
        var idx = nodeIndex(identity)
        if (idx < 0) return
        graphNodesModel.setProperty(idx, "nodeX", x)
        graphNodesModel.setProperty(idx, "nodeY", y)
        nodeLayoutRevision += 1
    }

    function typeName(value) {
        return ({person:"人物",group:"群体",item:"物品",location:"地点",event:"事件",scene:"场景",storyline:"故事线",other:"其他"})[value] || "节点"
    }

    function stateName(value) {
        return ({pending:"待审核",accepted:"已采纳",rejected:"已拒绝",complete:"已完成",running:"运行中",queued:"排队中",incomplete:"未完成",cancelled:"已取消",interrupted:"中断"})[value] || value || "未知"
    }

    function search() {
        var selected = []
        if (searchType.currentIndex > 0) selected.push(searchType.currentValue)
        var map = {"人物":"person","群体":"group","物品":"item","地点":"location","事件":"event","场景":"scene","故事线":"storyline","其他":"other"}
        var kinds = selected.length ? [map[selected[0]]] : []
        bridge.searchNodes(searchField.text, kinds)
    }

    function loadProjectData() {
        runGraphQuery()
        bridge.loadSources()
        bridge.loadCandidates("")
        bridge.loadRuns()
        bridge.loadProviderProfile()
        currentPage = "graph"
    }

    function applyProfileFields(profile) {
        providerProfile = profile || ({})
        syncProviderSelection()
        serviceModel.text = providerProfile.model || ""
        serviceBaseUrl.text = providerProfile.base_url || ""
        serviceKeyEnv.text = providerProfile.key_env || ""
        serviceReasoning.text = providerProfile.reasoning_effort || ""
        serviceTokenParameter.currentIndex = providerProfile.token_parameter === "max_completion_tokens" ? 1 : 0
        serviceOutputLimit.text = providerProfile.max_tokens === null || providerProfile.max_tokens === undefined
                ? "" : String(providerProfile.max_tokens)
        serviceContextWindow.text = String(providerProfile.context_window ?? 1000000)
        serviceOutputReserve.text = String(providerProfile.output_reserve ?? 131072)
        serviceCompactionReserve.text = String(providerProfile.compaction_reserve ?? 65536)
        serviceCompactionMaxTokens.text = String(providerProfile.compaction_max_tokens ?? 32768)
    }

    function syncProviderSelection() {
        if (!providerSelector || !providerProfile.provider) return
        for (var i = 0; i < providerCatalog.length; ++i) {
            if (providerCatalog[i].id === providerProfile.provider) {
                providerSelector.currentIndex = i
                return
            }
        }
    }

    function loadProviderProfile(providerId) {
        if (!providerId || providerId === providerProfile.provider) return
        profileNotice = "正在读取该供应商已保存的设置…"
        bridge.loadProviderProfileFor(providerId)
    }

    function saveCurrentProfile() {
        var maxTokens = null
        if (serviceOutputLimit.text.trim().length > 0) {
            maxTokens = Number(serviceOutputLimit.text)
            if (!Number.isSafeInteger(maxTokens) || maxTokens < 1) {
                profileNotice = "输出上限须为空（使用服务默认）或正整数。"
                return
            }
        }
        var contextSettings = {
            context_window: Number(serviceContextWindow.text),
            output_reserve: Number(serviceOutputReserve.text),
            compaction_reserve: Number(serviceCompactionReserve.text),
            compaction_max_tokens: Number(serviceCompactionMaxTokens.text)
        }
        if (![serviceContextWindow.text, serviceOutputReserve.text, serviceCompactionReserve.text,
              serviceCompactionMaxTokens.text].every(function(value) { return /^\d+$/.test(value.trim()) })) {
            profileNotice = "上下文与压缩参数须填写整数。"
            return
        }
        for (var field in contextSettings) {
            var minimum = field === "output_reserve" || field === "compaction_reserve" ? 0 : 1
            if (!Number.isSafeInteger(contextSettings[field]) || contextSettings[field] < minimum) {
                profileNotice = "上下文窗口与压缩最大输出须为正整数，预留须为非负整数。"
                return
            }
        }
        if (contextSettings.context_window <= Math.max(contextSettings.output_reserve, maxTokens || 0) + contextSettings.compaction_reserve) {
            profileNotice = "上下文窗口须大于有效输出预留与压缩预留之和。"
            return
        }
        profileNotice = "正在保存服务设置…"
        profileSaveInFlight = true
        bridge.saveProviderProfile({
            provider: providerProfile.provider,
            base_url: serviceBaseUrl.text.trim(),
            model: serviceModel.text.trim(),
            key_env: serviceKeyEnv.text.trim(),
            reasoning_effort: serviceReasoning.text.trim() || null,
            token_parameter: serviceTokenParameter.currentValue,
            max_tokens: maxTokens,
            context_window: contextSettings.context_window,
            output_reserve: contextSettings.output_reserve,
            compaction_reserve: contextSettings.compaction_reserve,
            compaction_max_tokens: contextSettings.compaction_max_tokens,
            timeout_seconds: providerProfile.timeout_seconds || 120
        })
    }

    function selectedProject() {
        return Object.keys(projectInfo).length > 0
    }

    function handleResult(kind, payload) {
        errorText = ""
        if (kind === "projects") {
            projectRowsModel.clear()
            projectItems = payload.projects || []
            for (var p = 0; p < projectItems.length; ++p) projectRowsModel.append(projectItems[p])
        } else if (kind === "project") {
            projectInfo = payload.project || {}
            timelineData = ({})
            timelinePath = []
            timelineSelections = ({})
            sourceItems = payload.sources || []
            sourceRowsModel.clear()
            for (var s = 0; s < sourceItems.length; ++s) sourceRowsModel.append(sourceItems[s])
            currentTrace = ({})
            currentDetail = ({})
            selectedNodeId = ""
            expandedRelationIds = ({})
            relationRequestPending = false
            selectedCandidateId = ""
            currentCandidate = ({})
            currentHierarchy = ({})
            candidateHierarchy = ({})
            expandedHierarchyIds = ({})
            expandedCandidateIds = ({})
            showSharedRoots = false
            showCandidateSharedRoots = false
            hierarchyRowsModel.clear()
            hierarchySharedRowsModel.clear()
            candidateHierarchyRowsModel.clear()
            candidateSharedRowsModel.clear()
            graphMode = "hierarchy"
            queryFocusId = ""; queryFocusName = ""; queryNodeTypes = null; queryRelations = null
            queryDistance = 2; queryDirection = "both"
            hierarchyFocusId = ""
            readingCut = ({})
            readingIndex = ({})
            readingNotice = ""
            bridge.loadReadingPositions()
            selectedSourceIds = []
            loadProjectData()
        } else if (kind === "graph_view") {
            setCanvas(payload.canvas, true)
            var allExpanded = ({})
            for (var qi=0; qi<(payload.canvas.nodes || []).length; ++qi) allExpanded[payload.canvas.nodes[qi].id] = true
            expandedRelationIds = allExpanded
            timelineData = payload.timeline || ({})
            queryTable = payload.table || ({})
            queryPaths = (payload.table.paths || []).filter(function(path) { return path.derived })
            queryJson = payload.query_json || ""
            currentHierarchy = payload.hierarchy || ({})
            if (!(currentHierarchy.organization_edges || []).length && graphMode === "hierarchy") graphMode = "relations"
            readingIndex = payload.index || ({})
            hierarchyFocusId = ""
            rebuildHierarchy(false)
            graphNotice = (payload.canvas.nodes || []).length + " 个节点 · " + (payload.canvas.edges || []).length + " 条直接关系" + (queryPaths.length ? " · " + queryPaths.length + " 条关联路径见分类表" : "") + (queryFocusId && (payload.canvas.focus || {}).matches_filter === false ? " · 焦点仅作定位，不计入筛选命中" : "")
            Qt.callLater(centerQueryFocus)
        } else if (kind === "reading_positions") {
            readingPositions = [{label: "全部已审核内容", source_id: "", start: 0, excerpt: ""}].concat(payload.items || [])
            readingNotice = payload.unregistered ? payload.unregistered + " 篇原文未登记阅读顺序，无法按进度回看。" : ""
        } else if (kind === "reading_hierarchy") {
            if (payload.requested_focus_id !== hierarchyFocusId || JSON.stringify(payload.cut) !== JSON.stringify(readingCut)) return
            hierarchyFocusId = payload.focus_id || ""
            currentHierarchy = payload.hierarchy || ({})
            readingIndex = payload.index || ({})
            currentDetail = ({})
            selectedNodeId = ""
            expandedHierarchyIds = ({})
            rebuildHierarchy(false)
        } else if (kind === "timeline") {
            if (JSON.stringify(payload.disclosure_cut || {}) !== JSON.stringify(readingCut)) return
            timelineData = payload
            validateTimelinePath()
        } else if (kind === "collapse") {
            relationRequestPending = false
            var collapsedIds = Object.assign({}, expandedRelationIds)
            delete collapsedIds[payload.center_id]
            expandedRelationIds = collapsedIds
            graphNotice = "已收拢该节点的一跳关系；其他展开节点仍保留。"
            for (var ci = graphEdgesModel.count - 1; ci >= 0; --ci)
                if ((payload.edges_removed || []).indexOf(graphEdgesModel.get(ci).assertion_id) >= 0) graphEdgesModel.remove(ci)
            for (var ni = graphNodesModel.count - 1; ni >= 0; --ni)
                if ((payload.nodes_removed || []).indexOf(graphNodesModel.get(ni).id) >= 0) graphNodesModel.remove(ni)
            nodeLayoutRevision += 1
        } else if (kind === "canvas") {
            unifiedQueryActive = false
            setCanvas(payload, true)
            if (!(currentHierarchy.organization_edges || currentHierarchy.edges || []).length && graphMode === "hierarchy") graphMode = "relations"
        } else if (kind === "hierarchy") {
            if (payload.candidate_id) {
                if (payload.candidate_id === selectedCandidateId) {
                    candidateHierarchy = payload
                    expandedCandidateIds = ({})
                    rebuildHierarchy(true)
                }
            } else {
                currentHierarchy = payload
                readingIndex = payload.event_index || ({})
                graphRevision = payload.revision || graphRevision
                if (!(payload.edges || []).length) graphMode = "relations"
                rebuildHierarchy(false)
            }
        } else if (kind === "expand") {
            relationRequestPending = false
            var expandedIds = Object.assign({}, expandedRelationIds)
            expandedIds[payload.center_id] = true
            expandedRelationIds = expandedIds
            setCanvas(payload, false)
            selectNode(payload.center_id)
        } else if (kind === "detail") {
            currentDetail = payload
        } else if (kind === "search") {
            searchResultsModel.clear()
            var found = payload.nodes || []
            for (var f = 0; f < found.length; ++f) searchResultsModel.append(found[f])
        } else if (kind === "candidates") {
            candidateItems = payload.items || []
            candidateListModel.clear()
            for (var c = 0; c < candidateItems.length; ++c) candidateListModel.append(candidateItems[c])
            var selectedStillExists = false
            for (var ci = 0; ci < candidateItems.length; ++ci)
                if (candidateItems[ci].id === selectedCandidateId) selectedStillExists = true
            if (selectedCandidateId && !selectedStillExists) selectedCandidateId = ""
            if (!selectedCandidateId && candidateItems.length) {
                selectedCandidateId = candidateItems[0].id
            }
            if (selectedCandidateId) bridge.candidateDetail(selectedCandidateId)
        } else if (kind === "candidate") {
            currentCandidate = payload.preview || {}
            candidateHierarchy = currentCandidate.hierarchy || ({})
            expandedCandidateIds = ({})
            rebuildHierarchy(true)
            candidateRowsModel.clear()
            var candidateObjects = currentCandidate.rows || []
            for (var cr = 0; cr < candidateObjects.length; ++cr) candidateRowsModel.append(candidateObjects[cr])
        } else if (kind === "review") {
            bridge.loadReadingPositions()
            graphNotice = payload.status === "accepted" ? "候选已采纳，正在刷新正式图…" : "候选已拒绝。"
            bridge.loadCandidates(candidateFilter === "全部" ? "" : candidateFilter)
            runGraphQuery()
        } else if (kind === "sources") {
            sourceItems = payload.items || []
            sourceRowsModel.clear()
            for (var si = 0; si < sourceItems.length; ++si) sourceRowsModel.append(sourceItems[si])
        } else if (kind === "source") {
            currentSource = payload.source || {}
            sourceTitle.text = currentSource.title || ""
            sourceTextArea.text = currentSource.text || ""
        } else if (kind === "source_imported") {
            bridge.loadReadingPositions()
            sourceItems = payload.sources || []
            sourceRowsModel.clear()
            for (var sii = 0; sii < sourceItems.length; ++sii) sourceRowsModel.append(sourceItems[sii])
            sourceTitle.text = ""
            sourceTextArea.text = ""
            errorText = "原文已导入；正文和哈希已由项目保存。"
        } else if (kind === "provider") {
            providerCatalog = payload.catalog || providerCatalog
            if (payload.profile) applyProfileFields(payload.profile)
            if (payload.profile) {
                profileNotice = profileSaveInFlight ? "服务设置已保存。" : "已读取该供应商在本工程中保存的配置。"
                profileSaveInFlight = false
            }
        } else if (kind === "run_started") {
            selectedRunId = payload.run_id || ""
            bridge.loadRuns()
            currentPage = "agent"
        } else if (kind === "runs") {
            runItems = payload.items || []
            runRowsModel.clear()
            for (var r = 0; r < runItems.length; ++r) runRowsModel.append(runItems[r])
            if (selectedRunId) bridge.loadTrace(selectedRunId)
            else if (runItems.length) {
                selectedRunId = runItems[0].run_id
                bridge.loadTrace(selectedRunId)
            }
        } else if (kind === "trace") {
            currentTrace = payload.trace || {}
        } else if (kind === "model_response") {
            currentResponse = payload.response || {}
        } else if (kind === "run_control") {
            if (selectedRunId) bridge.loadTrace(selectedRunId)
        }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) { root.handleResult(kind, payload) }
        function onErrorOccurred(kind, message) {
            if (kind === "expand" || kind === "collapse") root.relationRequestPending = false
            root.errorText = message
        }
        function onBusyChanged(value) { root.busy = value }
    }

    Component.onCompleted: bridge.refreshProjects()

    header: Rectangle {
        height: root.currentPage === "home" ? 68 : 76
        color: root.white
        border.color: root.line
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 24
            anchors.rightMargin: 24
            spacing: 18
            ColumnLayout {
                spacing: 0
                Label { text: "叙事图工作台"; font.pixelSize: 20; font.weight: Font.DemiBold; color: root.ink }
                Label { visible: root.currentPage !== "home"; text: root.projectInfo.title || "尚未打开工程"; font.pixelSize: 12; color: root.muted; elide: Text.ElideRight; Layout.maximumWidth: 360 }
            }
            Item { Layout.fillWidth: true }
            RowLayout {
                visible: root.currentPage !== "home"
                spacing: 6
                Repeater {
                    model: [
                        {key:"graph",label:"分析图"}, {key:"timeline",label:"时间轴"}, {key:"agent",label:"提取运行"},
                        {key:"candidates",label:"候选审核"}, {key:"sources",label:"原文与证据"}
                    ]
                    delegate: Button {
                        required property var modelData
                        text: modelData.label
                        flat: true
                        highlighted: modelData.key === "timeline" ? root.currentPage === "graph" && root.graphMode === "timeline" : root.currentPage === modelData.key && (modelData.key !== "graph" || root.graphMode !== "timeline")
                        onClicked: {
                            root.currentPage = modelData.key === "timeline" ? "graph" : modelData.key
                            if (modelData.key === "timeline") root.graphMode = "timeline"
                            if (modelData.key === "graph" && root.graphMode === "timeline") root.graphMode = "hierarchy"
                            if (modelData.key === "candidates") bridge.loadCandidates("")
                            else if (modelData.key === "sources") bridge.loadSources()
                            else if (modelData.key === "agent") { bridge.loadSources(); bridge.loadRuns() }
                        }
                    }
                }
            }
            Button {
                visible: root.currentPage !== "home"
                text: "切换工程"
                onClicked: { root.currentPage = "home"; bridge.refreshProjects() }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        StackLayout {
            id: pages
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: ({home:0,graph:1,candidates:2,sources:3,agent:4})[root.currentPage] || 0

            // Project landing page.
            Item {
                Rectangle { anchors.fill: parent; color: root.paper }
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 38
                    spacing: 28
                    ColumnLayout {
                        Layout.preferredWidth: 660
                        Layout.fillHeight: true
                        spacing: 18
                        Label { text: "最近工程"; font.pixelSize: 24; font.weight: Font.DemiBold; color: root.ink }
                        Label { text: "选择一个图工程继续浏览，或创建一个独立的新作品工程。"; color: root.muted; wrapMode: Text.Wrap }
                        ScrollView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            Column {
                                width: parent.width
                                spacing: 10
                                Repeater {
                                    id: projectList
                                    objectName: "projectList"
                                    model: projectRowsModel
                                    delegate: Rectangle {
                                        required property string path
                                        required property string name
                                        required property string title
                                        required property string status
                                        required property int revision
                                        width: parent.width
                                        height: 86
                                        radius: 12
                                        color: root.white
                                        border.color: root.line
                                        RowLayout {
                                            anchors.fill: parent; anchors.margins: 14; spacing: 12
                                            ColumnLayout {
                                                Layout.fillWidth: true; spacing: 4
                                                Label { text: title || name; color: root.ink; font.pixelSize: 15; font.weight: Font.DemiBold; elide: Text.ElideRight }
                                                Label { text: "修订 " + revision + " · " + (status === "ready" ? "可打开" : status || "图工程"); color: root.muted; font.pixelSize: 12 }
                                            }
                                            Button { text: "打开"; highlighted: true; onClicked: root.openProjectPath(path) }
                                        }
                                    }
                                }
                                Label { visible: projectRowsModel.count === 0; text: "还没有最近工程。新建工程后，从原文导入开始。"; color: root.muted; padding: 16 }
                            }
                        }
                    }
                    Rectangle {
                        Layout.preferredWidth: 400
                        Layout.preferredHeight: 330
                        Layout.alignment: Qt.AlignTop
                        radius: 18
                        color: root.white
                        border.color: root.line
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 24; spacing: 14
                            Label { text: "新建工程"; font.pixelSize: 20; font.weight: Font.DemiBold; color: root.ink }
                            Label { text: "新工程使用独立 v4 图数据库，不会改写旧工程。"; color: root.muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            TextField { id: projectName; objectName: "projectNameField"; placeholderText: "工程代号，例如 my-novel"; Layout.fillWidth: true }
                            Button {
                                objectName: "createProjectButton"
                                text: root.busy ? "正在创建…" : "创建并打开工程"
                                enabled: !root.busy && projectName.text.trim().length > 0
                                highlighted: true
                                Layout.fillWidth: true
                                onClicked: bridge.createProject(projectName.text.trim())
                            }
                            Label { text: "项目名仅用作本地文件名；项目标题可在工程中单独设置。"; color: root.muted; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        }
                    }
                }
            }

            // Analysis and free canvas.
            Item {
                Rectangle { anchors.fill: parent; color: root.paper }
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 18; spacing: 12
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        TextField { id: searchField; objectName: "searchField"; placeholderText: "搜索人物、事件、地点…"; Layout.preferredWidth: 320; onAccepted: root.search() }
                        ComboBox {
                            id: searchType
                            objectName: "typeFilter"
                            Layout.preferredWidth: 130
                            model: ["全部类型","人物","群体","物品","地点","事件","场景","故事线","其他"]
                            onActivated: root.search()
                        }
                        Button { text: "搜索"; highlighted: true; onClicked: root.search() }
                        Item { Layout.fillWidth: true }
                        Label { text: "正式修订 " + root.graphRevision; color: root.muted }
                        Button { text: "重新载入"; onClicked: root.runGraphQuery() }
                        Button { text: "适配视图"; visible: root.graphMode === "relations"; onClicked: root.fitGraph() }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { objectName: "queryFocusLabel"; text: root.queryFocusId ? "焦点：" + root.queryFocusName : "焦点：全图"; color: root.ink; Layout.maximumWidth: 190; elide: Text.ElideRight }
                        Button { objectName: "setQueryFocusButton"; text: "设为焦点"; enabled: Boolean(root.selectedNodeId); onClicked: root.setQueryFocus(root.selectedNodeId, (root.currentDetail.node || {}).name) }
                        Button { objectName: "clearQueryFocusButton"; text: "清除焦点"; enabled: Boolean(root.queryFocusId); onClicked: root.setQueryFocus("", "") }
                        Label { text: "关联距离"; color: root.muted }
                        ComboBox { objectName: "queryDistance"; model: ["1 跳","2 跳","3 跳"]; currentIndex: root.queryDistance - 1; onActivated: { root.queryDistance = currentIndex + 1; root.runGraphQuery() } }
                        ComboBox { objectName: "queryDirection"; model: ["双向","向外","向内"]; currentIndex: ["both","out","in"].indexOf(root.queryDirection); onActivated: { root.queryDirection = ["both","out","in"][currentIndex]; root.runGraphQuery() } }
                        Button { objectName: "nodeTypesButton"; text: "节点类型 · " + (root.queryNodeTypes === null ? "全部" : root.queryNodeTypes.length); onClicked: nodeTypesPopup.open() }
                        Button { objectName: "relationTypesButton"; text: "关系类别 · " + (root.queryRelations === null ? "全部" : root.queryRelations.length); onClicked: relationsPopup.open() }
                        Button { objectName: "resetQueryButton"; text: "重置筛选"; onClicked: root.resetQueryFilters() }
                        Button { text: "查询条件"; onClicked: queryDialog.open() }
                        Item { Layout.fillWidth: true }
                    }
                    RowLayout {
                        Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
                        Rectangle {
                            Layout.preferredWidth: 245; Layout.fillHeight: true; radius: 14; color: root.white; border.color: root.line
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 14; spacing: 10
                                Label { text: "搜索结果"; font.pixelSize: 16; font.weight: Font.DemiBold; color: root.ink }
                                
                                ScrollView {
                                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                    Column {
                                        width: parent.width; spacing: 6
                                        Repeater {
                                            model: searchResultsModel
                                            delegate: Button {
                                                required property string id
                                                required property string name
                                                required property string kind_label
                                                width: parent.width
                                                text: kind_label + " · " + name
                                                flat: true
                                                onClicked: root.selectNode(id)
                                            }
                                        }
                                        Label { visible: searchResultsModel.count === 0; text: "搜索正式图；待审候选不会混入。"; color: root.muted; wrapMode: Text.Wrap; padding: 8 }
                                    }
                                }
                            }
                        }
                        Rectangle {
                            id: viewport
                            objectName: "graphCanvas"
                            Layout.fillWidth: true; Layout.fillHeight: true; radius: 14; color: "#edf2f8"; border.color: root.line; clip: true
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 16; spacing: 10; z: 10
                                RowLayout {
                                    Layout.fillWidth: true
                                    Fusion.Button { objectName: "hierarchyModeButton"; text: "事件层级"; checkable: true; checked: root.graphMode === "hierarchy"; highlighted: checked; onClicked: root.graphMode = "hierarchy" }
                                    Fusion.Button { objectName: "timelineModeButton"; text: "时间轴"; checkable: true; checked: root.graphMode === "timeline"; highlighted: checked; onClicked: root.graphMode = "timeline" }
                                    Fusion.Button { objectName: "relationsModeButton"; text: "关系图"; checkable: true; checked: root.graphMode === "relations"; highlighted: checked; onClicked: root.graphMode = "relations" }
                                    Fusion.Button { objectName: "tableModeButton"; text: "分类表"; checkable: true; checked: root.graphMode === "table"; highlighted: checked; onClicked: root.graphMode = "table" }
                                    Item { Layout.fillWidth: true }
                                    Label { text: root.graphMode === "relations" ? "灰线：组织/参与 · 紫色箭头：时序" : ""; color: root.muted; font.pixelSize: 11 }
                                }
                                RowLayout {
                                    visible: root.graphMode === "hierarchy"; Layout.fillWidth: true
                                    Label { text: "阅读至"; color: root.muted }
                                    ComboBox {
                                        id: readingProgress; objectName: "readingProgress"; Layout.fillWidth: true
                                        model: root.readingPositions; textRole: "label"; enabled: !root.busy
                                        onActivated: root.chooseReadingPosition(currentIndex)
                                        ToolTip.visible: hovered
                                        ToolTip.text: currentIndex >= 0 ? root.readingPositions[currentIndex].excerpt : ""
                                    }
                                }
                                RowLayout {
                                    visible: root.graphMode === "hierarchy"; Layout.fillWidth: true
                                    Button { objectName: "enterEventButton"; text: "进入此事件"; enabled: !root.busy && root.selectedNodeId !== root.hierarchyFocusId && Boolean(root.hierarchyNode(root.selectedNodeId, false)) && ["event","scene","storyline"].indexOf(root.hierarchyNode(root.selectedNodeId, false).node_type) >= 0; onClicked: root.loadReadingView(root.selectedNodeId) }
                                    Button { objectName: "parentEventButton"; text: "返回上层"; enabled: !root.busy && root.hierarchyFocusId.length > 0; onClicked: root.focusParent() }
                                    Button { objectName: "eventOverviewButton"; text: "全貌"; enabled: !root.busy; onClicked: root.loadReadingView("") }
                                    Label { Layout.fillWidth: true; elide: Text.ElideRight; text: root.hierarchyFocusId && root.hierarchyNode(root.hierarchyFocusId, false) ? "当前主事件：" + root.hierarchyNode(root.hierarchyFocusId, false).name : "当前主事件：全貌"; color: root.muted }
                                }
                                Label { visible: root.graphMode === "hierarchy" && (root.readingNotice.length > 0 || Boolean(root.readingCut.source_id)); Layout.fillWidth: true; wrapMode: Text.Wrap; color: root.muted; text: root.readingNotice + (root.readingCut.source_id ? " 按此处已披露内容重建归属；未登记披露位置的关系不展示。" : "") }
                                ScrollView {
                                    visible: root.graphMode === "hierarchy"
                                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                    Column {
                                        width: viewport.width - 32; spacing: 7
                                        Repeater {
                                            model: hierarchyRowsModel
                                            delegate: Rectangle {
                                                required property string node_id
                                                required property string name
                                                required property string node_type
                                                required property int depth
                                                required property int child_count
                                                required property bool expanded
                                                required property string summary
                                                visible: root.nodeShown(node_id, node_type)
                                                objectName: "hierarchy_" + node_id
                                                x: Math.min(depth, 10) * 24; width: parent.width - x; height: visible ? hierarchyColumn.implicitHeight + 16 : 0
                                                radius: 9; color: root.typeColor(node_type); border.color: root.selectedNodeId === node_id ? root.accent : root.line
                                                ColumnLayout {
                                                    id: hierarchyColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8; spacing: 2
                                                    RowLayout {
                                                        Button { objectName: "hierarchyToggle_" + node_id; text: child_count ? (expanded ? "▾" : "▸") : "·"; enabled: child_count > 0; flat: true; onClicked: root.toggleHierarchy(node_id, false) }
                                                        Button { objectName: "hierarchyNode_" + node_id; text: root.typeName(node_type) + " · " + name; flat: true; highlighted: root.selectedNodeId === node_id; Layout.fillWidth: true; ToolTip.visible: hovered; ToolTip.text: name; onClicked: { root.selectNode(node_id); if (child_count) root.toggleHierarchy(node_id, false) } }
                                                        Label { text: child_count ? child_count + " 子项" : ""; color: root.muted; font.pixelSize: 11 }
                                                    }
                                                    Label { visible: summary.length > 0; text: summary; color: root.muted; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true }
                                                }
                                            }
                                        }
                                        Label { visible: hierarchyRowsModel.count === 0; text: "暂无已审核的故事线、场景或事件。"; color: root.muted; padding: 12 }
                                        Button { objectName: "sharedRootsToggle"; visible: hierarchySharedRowsModel.count > 0; text: (root.showSharedRoots ? "▾" : "▸") + " 人物、地点与物件（" + hierarchySharedRowsModel.count + "）"; flat: true; onClicked: root.showSharedRoots = !root.showSharedRoots }
                                        Repeater {
                                            model: (root.showSharedRoots || root.displayType !== "all") ? hierarchySharedRowsModel : null
                                            delegate: Button {
                                                required property string node_id
                                                required property string name
                                                required property string node_type
                                                visible: root.nodeShown(node_id, node_type)
                                                objectName: "shared_" + node_id
                                                text: root.typeName(node_type) + " · " + name
                                                flat: true; highlighted: root.selectedNodeId === node_id
                                                onClicked: root.selectNode(node_id)
                                            }
                                        }
                                        Label { visible: !(root.currentHierarchy.edges || []).length; text: "尚无直接包含关系；节点未分组。旧项目可切换到关系图继续浏览。"; color: root.muted; padding: 12 }
                                    }
                                }
                                ScrollView {
                                    id: queryTableScroll
                                    objectName: "queryTableView"
                                    visible: root.graphMode === "table"
                                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                    Column {
                                        width: queryTableScroll.availableWidth; spacing: 8
                                        Label { visible: !(root.queryTable.rows || []).length; text: "没有符合查询的内容"; color: root.muted; padding: 12 }
                                        Repeater {
                                            model: root.queryTable.groups || []
                                            delegate: Column {
                                                required property var modelData
                                                property var group: modelData
                                                width: parent.width; spacing: 4
                                                Label { text: root.typeName(group.key) + " · " + group.count; font.bold: true; color: root.ink; padding: 6 }
                                                Repeater {
                                                    model: (root.queryTable.rows || []).filter(function(row) { return group.node_ids.indexOf(row.id) >= 0 })
                                                    delegate: Button {
                                                        required property var modelData
                                                        objectName: "tableNode_" + modelData.id
                                                        width: parent.width; flat: true
                                                        text: modelData.name + " · " + root.typeName(modelData.node_type) + (modelData.focus_only ? " · 焦点定位" : "") + "\n" + (root.tableRelations(modelData) || "无直接关系") + " · 证据 " + root.tableEvidenceCount(modelData)
                                                        contentItem: Text { text: parent.text; font: parent.font; color: root.ink; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignLeft; verticalAlignment: Text.AlignVCenter }
                                                        highlighted: root.selectedNodeId === modelData.id
                                                        onClicked: root.selectNode(modelData.id)
                                                    }
                                                }
                                            }
                                        }
                                        Label { visible: root.queryPaths.length > 0; text: "关联路径 · " + root.queryPaths.length + " 条（多跳连接，不是直接断言）"; color: root.muted; wrapMode: Text.Wrap; width: parent.width; padding: 6 }
                                        Repeater {
                                            model: root.queryPaths
                                            delegate: Button {
                                                required property var modelData
                                                width: parent.width; flat: true
                                                text: root.queryNodeName(modelData.from_id) + " → " + modelData.length + " 跳关联路径 → " + root.queryNodeName(modelData.to_id) + "\n依据 " + modelData.assertion_ids.length + " 条直接断言 · 点击查看终点"
                                                contentItem: Text { text: parent.text; font: parent.font; color: root.ink; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignLeft; verticalAlignment: Text.AlignVCenter }
                                                onClicked: root.selectNode(modelData.to_id)
                                            }
                                        }
                                    }
                                }
                                ColumnLayout {
                                    id: timelineWorkspace
                                    visible: root.graphMode === "timeline"
                                    Layout.fillWidth: true; Layout.fillHeight: true; spacing: 8
                                    Label { Layout.fillWidth: true; text: "不同时间轴或相对组不能比较；未知时间不推定先后。"; color: root.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
                                    ScrollView {
                                        id: timelineScroll
                                        objectName: "timelineScroll"
                                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                        contentWidth: availableWidth
                                        Column {
                                            width: timelineScroll.availableWidth; spacing: 8
                                            Repeater {
                                                model: root.upperTimelineLayers()
                                                NestedEventAxis {
                                                    required property var modelData
                                                    width: parent.width; height: 200
                                                    controller: root; layerData: modelData
                                                    level: root.timelineLayers().findIndex(function(layer) { return layer.id === modelData.id })
                                                    selectedEventId: root.timelineSelections[modelData.id] || ""
                                                    onEventSelected: function(eventId) { root.clickTimelineEvent(modelData.id,eventId) }
                                                    onCloseRequested: root.closeTimelineLayer(level)
                                                }
                                            }
                                            Label { visible: root.timelineLayers().length === 1; width: parent.width; text: "从下方总览选择一个关键事件。带 ▴ 的事件可继续展开。"; color: root.muted; padding: 12; wrapMode: Text.Wrap }
                                        }
                                    }
                                    NestedEventAxis {
                                        objectName: "nestedAxis_root"
                                        Layout.fillWidth: true; Layout.preferredHeight: 200
                                        controller: root; layerData: root.timelineLayer("root"); level: 0
                                        selectedEventId: root.timelineSelections.root || ""
                                        onEventSelected: function(eventId) { root.clickTimelineEvent("root",eventId) }
                                    }
                                    Button { text: (root.showTemporalDetails ? "▾" : "▸") + " 时间关系依据（" + (root.timelineData.temporal_edges || []).length + "）"; flat: true; onClicked: root.showTemporalDetails = !root.showTemporalDetails }
                                    ScrollView {
                                        id: temporalDetailsScroll
                                        visible: root.showTemporalDetails
                                        Layout.fillWidth: true; Layout.preferredHeight: 170; clip: true
                                        contentWidth: availableWidth
                                        Column {
                                            width: temporalDetailsScroll.availableWidth; spacing: 8
                                            Repeater {
                                                model: root.timelineData.relative_axes || []
                                                RelativeNumberAxis {
                                                    required property var modelData
                                                    objectName: "numberAxis_" + modelData.id
                                                    width: parent.width; axisData: modelData; points: root.axisVisiblePoints(modelData)
                                                    onPointSelected: function(eventId) { root.selectNode(eventId) }
                                                }
                                            }
                                            Repeater {
                                                model: root.timelineData.temporal_edges || []
                                                Button {
                                                    required property var modelData
                                                    visible: (root.displayRelation === "all" || root.displayRelation === "temporal") && root.nodeShown(modelData.subject_id, "event") && root.nodeShown(modelData.object_id, "event")
                                                    width: parent.width; flat: true
                                                    text: root.eventName(modelData.subject_id) + " · " + root.boundaryLabel(modelData.temporal_left) + " " + root.temporalLabel(modelData.temporal_relation) + " → " + root.eventName(modelData.object_id) + " · " + root.boundaryLabel(modelData.temporal_right)
                                                    onClicked: root.selectNode(modelData.subject_id)
                                                }
                                            }
                                        }
                                    }
                                }
                                Item { visible: root.graphMode === "relations"; Layout.fillHeight: true }
                            }
                            Item {
                                id: graphScene
                                objectName: "graphScene"
                                width: 4000; height: 3000
                                transformOrigin: Item.TopLeft
                                scale: 0.8
                                z: 1
                                visible: root.graphMode === "relations"
                                Repeater {
                                    model: graphEdgesModel
                                    delegate: Item {
                                        id: edgeItem
                                        objectName: "edge_" + assertion_id
                                        required property string assertion_id
                                        required property string subject_id
                                        required property string object_id
                                        required property string label
                                        required property string semantic
                                        required property var model
                                        property var temporal_relation: model.temporal_relation
                                        property var temporal_left: model.temporal_left
                                        property var temporal_right: model.temporal_right
                                        property bool isTemporal: semantic === "temporal"
                                        property bool temporal_certain: model.temporal_certain === true
                                        property bool certainTemporal: isTemporal && temporal_certain
                                        property var sourceNode: root.nodeById(subject_id)
                                        property var targetNode: root.nodeById(object_id)
                                        property real startX: sourceNode ? sourceNode.x + 95 : 0
                                        property real startY: sourceNode ? sourceNode.y + 46 : 0
                                        property real endX: targetNode ? targetNode.x + 95 : 0
                                        property real endY: targetNode ? targetNode.y + 46 : 0
                                        property real dx: endX - startX
                                        property real dy: endY - startY
                                        width: Math.max(1, Math.sqrt(dx * dx + dy * dy))
                                        height: 2
                                        x: startX; y: startY
                                        visible: sourceNode !== null && targetNode !== null && root.nodeShown(subject_id, sourceNode.node_type) && root.nodeShown(object_id, targetNode.node_type) && (root.displayRelation === "all" || root.displayRelation === semantic || (root.displayRelation === "other" && ["temporal","containment","participation"].indexOf(semantic) < 0))
                                        Rectangle {
                                            width: parent.width; height: 2; y: -1
                                            color: edgeItem.isTemporal ? (edgeItem.certainTemporal ? "#8657b6" : "#aa7c37") : "#8293ad"
                                            Repeater { model: edgeItem.isTemporal ? Math.floor(edgeItem.width / 14) : 0; Rectangle { x: index * 14; width: 5; height: 2; color: "#edf2f8" } }
                                            Text { visible: edgeItem.certainTemporal; x: parent.width - 125; y: -10; text: ["same","same_time","overlaps"].indexOf(edgeItem.temporal_relation) >= 0 ? "↔" : "▶"; color: "#8657b6"; font.pixelSize: 18 }
                                            transform: Rotation { origin.x: 0; origin.y: 1; angle: Math.atan2(edgeItem.dy, edgeItem.dx) * 180 / Math.PI }
                                        }
                                        Label {
                                            objectName: "edgeLabel_" + edgeItem.assertion_id
                                            x: edgeItem.dx / 2 - width / 2
                                            y: edgeItem.dy / 2 - height / 2
                                            text: edgeItem.isTemporal ? (edgeItem.certainTemporal ? "" : "待定 · ") + root.temporalLabel(edgeItem.temporal_relation) + " · " + root.boundaryLabel(edgeItem.temporal_left) + " → " + root.boundaryLabel(edgeItem.temporal_right) : (edgeItem.label || "直接断言")
                                            color: root.ink; font.pixelSize: 11
                                            leftPadding: 7; rightPadding: 7; topPadding: 3; bottomPadding: 3
                                            background: Rectangle { radius: 5; color: "#f8fbff"; border.color: "#d8e1ec" }
                                        }
                                    }
                                }
                                Repeater {
                                    model: graphNodesModel
                                    delegate: Rectangle {
                                        id: nodeCard
                                        required property string id
                                        required property string name
                                        required property string node_type
                                        required property string kind_label
                                        required property string summary
                                        required property real nodeX
                                        required property real nodeY
                                        visible: root.nodeShown(id, node_type)
                                        objectName: "node_" + id
                                        width: 190; height: 92; radius: 14
                                        color: root.typeColor(node_type)
                                        border.width: root.selectedNodeId === id || root.queryFocusId === id ? 2 : 1
                                        border.color: root.queryFocusId === id ? root.green : root.selectedNodeId === id ? root.accent : "#d3deeb"
                                        x: nodeX; y: nodeY
                                        z: root.selectedNodeId === id ? 5 : 2
                                        Column {
                                            anchors.fill: parent; anchors.margins: 12; spacing: 5
                                            Label { text: (root.queryFocusId === nodeCard.id ? "焦点 · " : "") + nodeCard.kind_label; color: root.muted; font.pixelSize: 11 }
                                            Label { text: nodeCard.name; color: root.ink; font.pixelSize: 14; font.weight: Font.DemiBold; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; width: parent.width }
                                            
                                        }
                                        MouseArea {
                                            anchors.fill: parent
                                            drag.target: nodeCard
                                            drag.threshold: 6
                                            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                                            onClicked: { root.selectNode(nodeCard.id); root.toggleRelationNode(nodeCard.id) }
                                            onPositionChanged: {
                                                if (drag.active)
                                                    root.previewNodePosition(nodeCard.id, nodeCard.x, nodeCard.y)
                                            }
                                            onReleased: {
                                                root.updateNodePosition(nodeCard.id, nodeCard.x, nodeCard.y)
                                                bridge.setNodePosition(nodeCard.id, nodeCard.x, nodeCard.y)
                                            }
                                        }
                                    }
                                }
                            }
                            MouseArea {
                                id: panArea
                                anchors.fill: parent
                                z: 0
                                visible: root.graphMode === "relations"
                                acceptedButtons: Qt.LeftButton
                                property real lastX: 0
                                property real lastY: 0
                                onPressed: function(mouse) { lastX = mouse.x; lastY = mouse.y; cursorShape = Qt.ClosedHandCursor }
                                onPositionChanged: function(mouse) {
                                    if (pressed) { graphScene.x += mouse.x - lastX; graphScene.y += mouse.y - lastY; lastX = mouse.x; lastY = mouse.y }
                                }
                                onReleased: cursorShape = Qt.OpenHandCursor
                                onWheel: function(wheel) {
                                    var oldScale = graphScene.scale
                                    var factor = wheel.angleDelta.y > 0 ? 1.12 : 0.89
                                    var newScale = Math.max(0.35, Math.min(2.2, oldScale * factor))
                                    var px = (wheel.x - graphScene.x) / oldScale
                                    var py = (wheel.y - graphScene.y) / oldScale
                                    graphScene.scale = newScale
                                    graphScene.x = wheel.x - px * newScale
                                    graphScene.y = wheel.y - py * newScale
                                    wheel.accepted = true
                                }
                            }
                            Column {
                                anchors.centerIn: parent; spacing: 6
                                visible: root.graphMode === "relations" && root.visibleNodeCount() === 0
                                Label { anchors.horizontalCenter: parent.horizontalCenter; text: "没有符合查询的内容"; color: root.ink; font.pixelSize: 18; font.weight: Font.DemiBold }
                                Label { anchors.horizontalCenter: parent.horizontalCenter; text: "可调整焦点、距离或筛选后继续浏览。"; color: root.muted }
                            }
                            Rectangle {
                                anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: 12
                                visible: root.graphMode === "relations" && (root.graphNotice.length > 0 || root.graphTruncated)
                                radius: 7; color: "#ffffffed"; border.color: root.line; width: noteText.implicitWidth + 24; height: noteText.implicitHeight + 12
                                Label { id: noteText; anchors.centerIn: parent; text: root.graphTruncated ? root.graphNotice + " 初始锚点已截取。" : root.graphNotice; color: root.muted; font.pixelSize: 11 }
                            }
                        }
                        Rectangle {
                            id: detailPanel
                            objectName: "detailPanel"
                            Layout.preferredWidth: 320; Layout.fillHeight: true; radius: 14; color: root.white; border.color: root.line
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 16; spacing: 10
                                Label { text: root.currentDetail.node ? root.currentDetail.node.name : (root.selectedNodeId ? "正在读取节点…" : "节点详情"); font.pixelSize: 17; font.weight: Font.DemiBold; color: root.ink; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                Label { visible: Boolean(root.currentDetail.node); text: root.typeName(root.currentDetail.node ? root.currentDetail.node.node_type : "") + " · 修订 " + (root.currentDetail.revision || "—"); color: root.muted; font.pixelSize: 12 }
                                Label { visible: Boolean(root.currentDetail.node && root.currentDetail.node.summary); text: root.currentDetail.node ? root.currentDetail.node.summary : ""; color: root.muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                Label { visible: root.selectedEventHistory().length > 0; text: "已披露归属记录：" + root.selectedEventHistory(); color: root.muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                Label { visible: root.hierarchyPath(root.selectedNodeId).length > 0; text: "父路径：" + root.hierarchyPath(root.selectedNodeId); color: root.muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                Label { visible: Boolean(root.hierarchyNode(root.selectedNodeId, false) && root.hierarchyNode(root.selectedNodeId, false).child_ids.length); text: "直接子项：" + (root.hierarchyNode(root.selectedNodeId, false) ? root.hierarchyNode(root.selectedNodeId, false).child_ids.map(function(id) { var n = root.hierarchyNode(id, false); return n ? n.name : id }).join("、") : ""); color: root.muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                Label { visible: Boolean(root.hierarchyNode(root.selectedNodeId, false) && root.hierarchyNode(root.selectedNodeId, false).child_ids.length); text: "子项概述来自各自节点；不会作为当前节点的直接事实。"; color: root.muted; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 11 }
                                Button { visible: root.selectedNodeId.length > 0; text: "以此节点为焦点"; Layout.fillWidth: true; onClicked: root.setQueryFocus(root.selectedNodeId, (root.currentDetail.node || {}).name) }
                                Button { visible: root.selectedNodeId.length > 0 && root.graphMode === "relations"; text: "折叠／恢复当前查询内的关联"; Layout.fillWidth: true; onClicked: root.toggleRelationNode(root.selectedNodeId) }
                                Rectangle { Layout.fillWidth: true; height: 1; color: root.line }
                                Label { text: "相关节点 · 主事件优先"; color: root.ink; font.weight: Font.DemiBold }
                                ScrollView {
                                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                    Column {
                                        width: parent.width; spacing: 8
                                        Repeater {
                                            model: root.orderedRelatedNodes()
                                            delegate: Button {
                                                required property var modelData
                                                width: parent.width
                                                text: root.typeName(modelData.node_type) + " · " + modelData.name
                                                flat: true
                                                onClicked: root.selectNode(modelData.id)
                                            }
                                        }
                                        Label { visible: (root.currentDetail.assertions || []).length > 0; text: "断言与证据"; color: root.ink; font.weight: Font.DemiBold; padding: 4 }
                                        Repeater {
                                            model: root.currentDetail.assertions || []
                                            delegate: Rectangle {
                                                required property var modelData
                                                width: parent.width; height: assertionColumn.implicitHeight + 20; radius: 10; color: "#f7f9fc"; border.color: root.line
                                                Column {
                                                    id: assertionColumn
                                                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10; spacing: 6
                                                    Label {
                                                        width: parent.width; wrapMode: Text.Wrap
                                                        text: (modelData.subject_label || "节点") + " → " + (modelData.predicate_label || modelData.semantic || "断言") + " → " + (modelData.object_label || "未知")
                                                        color: root.ink; font.pixelSize: 12; font.weight: Font.DemiBold
                                                    }
                                                    Label { visible: Boolean(modelData.organization_label); text: modelData.organization_label || ""; color: modelData.organization_status === "active" ? root.green : root.muted; font.pixelSize: 11; font.weight: Font.DemiBold }
                                                    Label { visible: Boolean(modelData.role); text: "角色：" + modelData.role; color: root.muted; font.pixelSize: 11 }
                                                    Label { visible: Boolean(modelData.scope || modelData.derived_status); text: "范围：" + (modelData.scope || "未说明") + " · 状态：" + (modelData.derived_status || "已记录"); color: root.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
                                                    Repeater {
                                                        model: modelData.evidence || []
                                                        delegate: Label {
                                                            required property var modelData
                                                            width: parent.width; wrapMode: Text.Wrap
                                                            text: "“" + (modelData.quote || modelData.excerpt || "证据引文") + "” · " + (modelData.source_title || "原文来源")
                                                            color: root.green; font.pixelSize: 11
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                        Label { visible: (root.currentDetail.assertions || []).length === 0 && (root.currentDetail.related_nodes || []).length === 0; text: "选中节点后，这里显示直接关联、范围与原文证据。"; color: root.muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                    }
                                }
                                Label { visible: root.selectedNodeId.length > 0; text: "技术详情 · " + root.selectedNodeId; color: root.muted; font.pixelSize: 10; elide: Text.ElideMiddle; Layout.fillWidth: true }
                            }
                        }
                    }
                }
            }

            // Candidate review page.
            Item {
                Rectangle { anchors.fill: parent; color: root.paper }
                RowLayout {
                    anchors.fill: parent; anchors.margins: 20; spacing: 14
                    Rectangle {
                        Layout.preferredWidth: 360; Layout.fillHeight: true; radius: 14; color: root.white; border.color: root.line
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 16; spacing: 10
                            RowLayout {
                                Label { text: "候选批次"; font.pixelSize: 18; font.weight: Font.DemiBold; color: root.ink }
                                Item { Layout.fillWidth: true }
                                ComboBox { id: candidateFilterBox; model: ["全部","待审核","已采纳","已拒绝"]; onActivated: { root.candidateFilter = ({"全部":"全部","待审核":"pending","已采纳":"accepted","已拒绝":"rejected"})[currentValue]; bridge.loadCandidates(root.candidateFilter === "全部" ? "" : root.candidateFilter) } }
                            }
                            ScrollView {
                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                Column {
                                    width: parent.width; spacing: 8
                                    Repeater {
                                        id: candidateList
                                        objectName: "candidateList"
                                        model: candidateListModel
                                        delegate: Button {
                                            required property string id
                                            required property string status
                                            required property int object_count
                                            required property string source
                                            required property string note
                                            width: parent.width; height: 74; flat: false
                                            highlighted: root.selectedCandidateId === id
                                            text: "候选 · " + object_count + " 项 · " + root.stateName(status) + (note ? "\n" + note : "")
                                            onClicked: { root.selectedCandidateId = id; root.candidateHierarchy = ({}); candidateHierarchyRowsModel.clear(); candidateSharedRowsModel.clear(); bridge.candidateDetail(id) }
                                        }
                                    }
                                    Label { visible: candidateListModel.count === 0; text: "暂无候选。完成一轮分析后，结果会在这里等待审核。"; color: root.muted; wrapMode: Text.Wrap; padding: 10 }
                                }
                            }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.fillHeight: true; radius: 14; color: root.white; border.color: root.line
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 20; spacing: 10
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: root.currentCandidate.candidate ? "候选预览 · " + root.currentCandidate.rows.length + " 项" : "选择候选查看预览"; font.pixelSize: 19; font.weight: Font.DemiBold; color: root.ink }
                                Item { Layout.fillWidth: true }
                                Button {
                                    objectName: "candidateApproveButton"
                                    text: "接受并进入正式图"
                                    visible: Boolean(root.currentCandidate.candidate && root.currentCandidate.candidate.status === "pending")
                                    enabled: visible && !root.busy
                                    highlighted: true
                                    onClicked: bridge.acceptCandidate(root.currentCandidate.candidate.id, root.currentCandidate.candidate.version)
                                }
                                Button {
                                    objectName: "candidateRejectButton"
                                    text: "拒绝候选"
                                    visible: Boolean(root.currentCandidate.candidate && root.currentCandidate.candidate.status === "pending")
                                    enabled: visible && !root.busy
                                    onClicked: bridge.rejectCandidate(root.currentCandidate.candidate.id, root.currentCandidate.candidate.version)
                                }
                            }
                            Label {
                                visible: Boolean(root.currentCandidate.candidate)
                                text: "状态：" + root.stateName(root.currentCandidate.candidate ? root.currentCandidate.candidate.status : "") + " · 版本 " + (root.currentCandidate.candidate ? root.currentCandidate.candidate.version : "—") + " · 正式修订 " + (root.currentCandidate.candidate ? root.currentCandidate.candidate.expected_revision : "—")
                                color: root.muted
                            }
                            Rectangle {
                                visible: candidateHierarchyRowsModel.count > 0 || candidateSharedRowsModel.count > 0
                                Layout.fillWidth: true; Layout.preferredHeight: Math.min(260, candidateHierarchyRowsModel.count * 46 + 52)
                                radius: 9; color: "#eef4ff"; border.color: root.line
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 8
                                    Label { text: "候选事件层级 · 仅预览，须人工审核"; color: root.ink; font.weight: Font.DemiBold }
                                    ScrollView {
                                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                        Column {
                                            width: parent.width; spacing: 2
                                            Repeater {
                                                model: candidateHierarchyRowsModel
                                                delegate: Row {
                                                    required property string node_id
                                                    required property string name
                                                    required property string node_type
                                                    required property int depth
                                                    required property int child_count
                                                    required property bool expanded
                                                    x: Math.min(depth, 10) * 20; spacing: 5
                                                    Button { objectName: "candidateHierarchyToggle_" + node_id; text: child_count ? (expanded ? "▾" : "▸") : "·"; flat: true; enabled: child_count > 0; onClicked: root.toggleHierarchy(node_id, true) }
                                                    Label { text: root.typeName(node_type) + " · " + name + (child_count ? "（" + child_count + " 子项）" : ""); anchors.verticalCenter: parent.verticalCenter; color: root.ink }
                                                }
                                            }
                                            Button { visible: candidateSharedRowsModel.count > 0; text: (root.showCandidateSharedRoots ? "▾" : "▸") + " 人物、地点与物件（" + candidateSharedRowsModel.count + "）"; flat: true; onClicked: root.showCandidateSharedRoots = !root.showCandidateSharedRoots }
                                            Repeater {
                                                model: root.showCandidateSharedRoots ? candidateSharedRowsModel : null
                                                delegate: Label {
                                                    required property string name
                                                    required property string node_type
                                                    text: root.typeName(node_type) + " · " + name
                                                    color: root.muted; leftPadding: 18
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            ScrollView {
                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                Column {
                                    width: parent.width; spacing: 8
                                    Repeater {
                                        model: candidateRowsModel
                                        delegate: Rectangle {
                                            required property string kind_label
                                            required property string qualified_label
                                            required property string label
                                            required property string summary
                                            required property var object
                                            width: parent.width; height: previewColumn.implicitHeight + 22; radius: 10; color: "#f8fafd"; border.color: root.line
                                            Column {
                                                id: previewColumn
                                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12; spacing: 5
                                                Label { text: kind_label + " · " + (qualified_label || label); color: root.ink; font.weight: Font.DemiBold; wrapMode: Text.Wrap; width: parent.width }
                                                Label { text: summary; visible: summary.length > 0; color: root.muted; font.pixelSize: 12; wrapMode: Text.Wrap; width: parent.width }
                                                Label { text: object && object.quote ? "“" + object.quote + "”" : ""; visible: Boolean(object && object.quote); color: root.green; font.pixelSize: 12; wrapMode: Text.Wrap; width: parent.width }
                                            }
                                        }
                                    }
                                    Label { visible: candidateRowsModel.count === 0; text: "候选对象会以名称、关系、引文和来源呈现；完整标识留在技术详情。"; color: root.muted; wrapMode: Text.Wrap; padding: 12 }
                                }
                            }
                            Label { visible: Boolean(root.currentCandidate.candidate); text: "候选 ID · " + (root.currentCandidate.candidate ? root.currentCandidate.candidate.id : ""); color: root.muted; font.pixelSize: 10; elide: Text.ElideMiddle }
                        }
                    }
                }
            }

            // Sources and evidence import page.
            Item {
                Rectangle { anchors.fill: parent; color: root.paper }
                RowLayout {
                    anchors.fill: parent; anchors.margins: 20; spacing: 14
                    Rectangle {
                        Layout.preferredWidth: 340; Layout.fillHeight: true; radius: 14; color: root.white; border.color: root.line
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 16; spacing: 10
                            RowLayout {
                                Label { text: "工程原文"; font.pixelSize: 18; font.weight: Font.DemiBold; color: root.ink }
                                Item { Layout.fillWidth: true }
                                Button { text: "刷新"; onClicked: bridge.loadSources() }
                            }
                            ScrollView {
                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                Column {
                                    width: parent.width; spacing: 7
                                    Repeater {
                                        model: sourceRowsModel
                                        delegate: Button {
                                            required property string id
                                            required property string title
                                            required property int length
                                            required property string hash
                                            width: parent.width; height: 68; flat: true
                                            text: (title || "未命名章节") + "\n" + length + " 字 · " + (hash ? hash.slice(0, 10) + "…" : "无哈希")
                                            onClicked: { root.selectedSourceId = id; bridge.sourceText(id) }
                                        }
                                    }
                                    Label { visible: sourceRowsModel.count === 0; text: "尚未导入原文。导入后可以定位证据并启动分析。"; color: root.muted; wrapMode: Text.Wrap; padding: 10 }
                                }
                            }
                            Label { text: "哈希用于校验原文版本；日常阅读以章节标题为主。"; color: root.muted; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.fillHeight: true; radius: 14; color: root.white; border.color: root.line
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 20; spacing: 10
                            Label { text: "导入原文"; font.pixelSize: 20; font.weight: Font.DemiBold; color: root.ink }
                            TextField { id: sourceTitle; objectName: "sourceTitleField"; placeholderText: "章节标题"; Layout.fillWidth: true }
                            TextArea {
                                id: sourceTextArea; objectName: "sourceTextArea"
                                Layout.fillWidth: true; Layout.fillHeight: true
                                placeholderText: "粘贴完整章节文本。原文作为不可变来源保存，后续候选通过证据锚引用。"
                                wrapMode: TextEdit.Wrap
                                selectByMouse: true
                            }
                            RowLayout {
                                Button {
                                    objectName: "importSourceButton"
                                    text: "保存原文"
                                    enabled: !root.busy && sourceTitle.text.trim().length > 0 && sourceTextArea.text.trim().length > 0
                                    highlighted: true
                                    onClicked: bridge.importSource(sourceTitle.text.trim(), sourceTextArea.text)
                                }
                                Item { Layout.fillWidth: true }
                                Label { text: root.currentSource.hash ? "当前原文 SHA-256：" + root.currentSource.hash : "原文导入后可在左侧打开并查看。"; color: root.muted; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.maximumWidth: 430 }
                            }
                        }
                    }
                }
            }

            // Observable analysis runs.
            Item {
                Rectangle { anchors.fill: parent; color: root.paper }
                RowLayout {
                    anchors.fill: parent; anchors.margins: 18; spacing: 12
                    Rectangle {
                        Layout.preferredWidth: 330; Layout.fillHeight: true; radius: 14; color: root.white; border.color: root.line
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 14; spacing: 9
                            Label { text: "启动分析"; font.pixelSize: 18; font.weight: Font.DemiBold; color: root.ink }
                            Label { text: root.providerProfile.model ? "当前模型：" + root.providerProfile.provider + " / " + root.providerProfile.model : "服务配置由项目保存；启动任务使用当前配置。"; color: root.muted; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            Button { text: root.showServiceSettings ? "收起服务设置" : "服务设置"; flat: true; onClicked: root.showServiceSettings = !root.showServiceSettings }
                            ScrollView {
                                id: serviceSettingsScroll
                                objectName: "serviceSettingsScroll"
                                visible: root.showServiceSettings
                                Layout.fillWidth: true
                                Layout.preferredHeight: Math.min(serviceSettingsFields.implicitHeight, Math.max(60, Math.min(280, root.height - 680)))
                                contentWidth: availableWidth
                                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                                clip: true
                                ColumnLayout {
                                id: serviceSettingsFields
                                width: serviceSettingsScroll.availableWidth
                                spacing: 5
                                ComboBox {
                                    id: providerSelector
                                    objectName: "providerSelect"
                                    Layout.fillWidth: true
                                    model: root.providerCatalog
                                    textRole: "name"
                                    valueRole: "id"
                                    enabled: !root.busy && count > 0
                                    onActivated: root.loadProviderProfile(currentValue)
                                }
                                Label { text: "切换仅读取该供应商已保存的配置；保存按钮才会写入。密钥值不在界面保存。"; color: root.muted; font.pixelSize: 10; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                TextField { id: serviceModel; objectName: "serviceModelField"; placeholderText: "精确模型 ID"; Layout.fillWidth: true }
                                TextField { id: serviceBaseUrl; placeholderText: "兼容服务基础地址"; Layout.fillWidth: true }
                                RowLayout {
                                    TextField { id: serviceKeyEnv; placeholderText: "密钥环境变量名"; Layout.fillWidth: true }
                                    TextField { id: serviceReasoning; placeholderText: "推理强度（可留空）"; Layout.fillWidth: true }
                                }
                                RowLayout {
                                    TextField { id: serviceOutputLimit; objectName: "serviceOutputLimitField"; placeholderText: "单次生成上限；留空用服务默认"; Layout.fillWidth: true }
                                    ComboBox { id: serviceTokenParameter; model: ["max_tokens","max_completion_tokens"]; Layout.preferredWidth: 140 }
                                }
                                Label { text: "上下文与压缩预算"; color: root.ink; font.weight: Font.DemiBold }
                                GridLayout {
                                    columns: 2; Layout.fillWidth: true
                                    Label { text: "上下文窗口（token）"; color: root.muted; font.pixelSize: 11 }
                                    TextField { id: serviceContextWindow; objectName: "serviceContextWindowField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly }
                                    Label { text: "输出预留（token）"; color: root.muted; font.pixelSize: 11 }
                                    TextField { id: serviceOutputReserve; objectName: "serviceOutputReserveField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly }
                                    Label { text: "压缩预留（token）"; color: root.muted; font.pixelSize: 11 }
                                    TextField { id: serviceCompactionReserve; objectName: "serviceCompactionReserveField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly }
                                    Label { text: "压缩最大输出（token）"; color: root.muted; font.pixelSize: 11 }
                                    TextField { id: serviceCompactionMaxTokens; objectName: "serviceCompactionMaxTokensField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly }
                                }
                                Label {
                                    objectName: "serviceHistoryBudgetLabel"
                                    text: "可用历史阈值：" + Math.max(0, Number(serviceContextWindow.text) - Math.max(Number(serviceOutputReserve.text), Number(serviceOutputLimit.text)) - Number(serviceCompactionReserve.text)) + " token。有效输出预留取输出预留与单次生成上限的较大值；单次生成上限仍独立设置。"
                                    color: root.muted; font.pixelSize: 10; wrapMode: Text.Wrap; Layout.fillWidth: true
                                }
                                RowLayout {
                                    Button { objectName: "saveServiceSettingsButton"; text: "保存到本工程"; enabled: !root.busy; onClicked: root.saveCurrentProfile() }
                                    Label { text: root.profileNotice; color: root.muted; font.pixelSize: 10; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                }
                                }
                            }
                            Label { text: "选择原文"; color: root.ink; font.weight: Font.DemiBold }
                            ScrollView {
                                Layout.fillWidth: true; Layout.preferredHeight: 160; clip: true
                                Column {
                                    width: parent.width; spacing: 4
                                    Repeater {
                                        model: sourceRowsModel
                                        delegate: CheckBox {
                                            required property string id
                                            required property string title
                                            text: title
                                            checked: root.selectedSourceIds.indexOf(id) >= 0
                                            onToggled: {
                                                var copy = root.selectedSourceIds.slice()
                                                var index = copy.indexOf(id)
                                                if (checked && index < 0) copy.push(id)
                                                else if (!checked && index >= 0) copy.splice(index, 1)
                                                root.selectedSourceIds = copy
                                            }
                                        }
                                    }
                                    Label { visible: sourceRowsModel.count === 0; text: "请先导入原文。"; color: root.muted }
                                }
                            }
                            TextArea { id: taskField; objectName: "analysisTaskField"; Layout.fillWidth: true; Layout.preferredHeight: 92; text: root.taskText; wrapMode: TextEdit.Wrap; placeholderText: "写明本次分析目标"; onTextChanged: root.taskText = text }
                            RowLayout {
                                Label { text: "检索阶段回合"; color: root.muted; font.pixelSize: 12 }
                                SpinBox { id: researchRounds; from: 1; to: 20; value: 3; editable: true }
                                Item { Layout.fillWidth: true }
                            }
                            Label { text: "工具动作不设次数上限；任务可随时取消。候选始终待人工审核。"; color: root.muted; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            Button {
                                objectName: "startAnalysisButton"
                                text: root.busy ? "处理中…" : "启动图分析"
                                enabled: !root.busy && root.selectedSourceIds.length > 0
                                highlighted: true; Layout.fillWidth: true
                                onClicked: bridge.startAnalysis(root.selectedSourceIds, taskField.text.trim(), {research_rounds: researchRounds.value, max_tokens_per_round:"inherit"})
                            }
                            Rectangle { Layout.fillWidth: true; height: 1; color: root.line }
                            RowLayout {
                                Label { text: "历史运行"; color: root.ink; font.weight: Font.DemiBold }
                                Item { Layout.fillWidth: true }
                                Button { text: "刷新"; onClicked: bridge.loadRuns() }
                            }
                            ScrollView {
                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                Column {
                                    width: parent.width; spacing: 5
                                    Repeater {
                                        id: runList
                                        objectName: "runList"
                                        model: runRowsModel
                                        delegate: Button {
                                            required property string run_id
                                            required property string status
                                            required property string phase
                                            required property string model
                                            required property int rounds
                                            width: parent.width; height: 62; flat: true; highlighted: root.selectedRunId === run_id
                                            text: root.stateName(status) + " · " + (model || "模型未知") + "\n" + root.phaseName(phase) + " · " + rounds + " 轮"
                                            onClicked: { root.selectedRunId = run_id; bridge.loadTrace(run_id) }
                                        }
                                    }
                                    Label { visible: runRowsModel.count === 0; text: "尚无分析运行。"; color: root.muted }
                                }
                            }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.fillHeight: true; radius: 14; color: root.white; border.color: root.line
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 18; spacing: 10
                            RowLayout {
                                Label { text: root.currentTrace.run_id ? "运行轨迹 · " + root.stateName(root.currentTrace.status) : "运行轨迹"; font.pixelSize: 19; font.weight: Font.DemiBold; color: root.ink }
                                Item { Layout.fillWidth: true }
                                Button { text: "刷新轨迹"; enabled: Boolean(root.selectedRunId); onClicked: bridge.loadTrace(root.selectedRunId) }
                                Button { text: "暂停"; enabled: root.currentTrace.status === "running" && !root.currentTrace.control?.paused; onClicked: bridge.pauseRun(root.selectedRunId) }
                                Button { text: "恢复"; enabled: root.currentTrace.status === "running" && Boolean(root.currentTrace.control?.paused); onClicked: bridge.resumeRun(root.selectedRunId) }
                                Button { text: "取消"; enabled: root.currentTrace.status === "running" || root.currentTrace.status === "queued"; onClicked: bridge.cancelRun(root.selectedRunId) }
                            }
                            Label {
                                visible: Boolean(root.currentTrace.run_id)
                                text: "目标：" + (root.currentTrace.task || "未填写") + "\n阶段：" + root.phaseName(root.currentTrace.phase) + " · 请求状态：" + root.requestName(root.currentTrace.request_state) + " · 模型请求：" + (root.currentTrace.rounds || 0) + " · 工具动作：" + (root.currentTrace.tool_count || 0)
                                color: root.muted; wrapMode: Text.Wrap; Layout.fillWidth: true
                            }
                            Label { visible: Boolean(root.currentTrace.run_id); text: (root.currentTrace.provider || "未知") + " / " + (root.currentTrace.model || "未知") + " · " + (root.currentTrace.prompt_version || "历史版本未知") + " · Usage：" + (root.currentTrace.usage && root.currentTrace.usage.total_tokens !== undefined ? root.currentTrace.usage.total_tokens + " tokens" : "未报告"); color: root.muted; font.pixelSize: 12 }
                            Label { visible: Boolean(root.currentTrace.error); text: root.currentTrace.error || ""; color: root.red; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            RowLayout {
                                visible: Boolean(root.currentTrace.run_id && root.currentTrace.candidate_ids && root.currentTrace.candidate_ids.length)
                                Label { text: "本运行候选：" + (root.currentTrace.candidate_ids ? root.currentTrace.candidate_ids.length : 0) + " 项 · 等待候选审核页查看"; color: root.green }
                                Item { Layout.fillWidth: true }
                                Button { text: "查看候选"; onClicked: { root.currentPage = "candidates"; bridge.loadCandidates("") } }
                            }
                            Label { text: "记录到的工具与公开动作"; color: root.ink; font.weight: Font.DemiBold }
                            ScrollView {
                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                Column {
                                    width: parent.width; spacing: 8
                                    Repeater {
                                        model: root.currentTrace.steps || []
                                        delegate: Rectangle {
                                            required property var modelData
                                            width: parent.width; height: stepColumn.implicitHeight + 18; radius: 10; color: "#f7f9fc"; border.color: root.line
                                            Column {
                                                id: stepColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10; spacing: 5
                                                Label { text: "第 " + (modelData.round || "?") + " 轮 · " + root.phaseName(modelData.phase) + " · " + (modelData.finish_reason || "未结束"); color: root.ink; font.weight: Font.DemiBold }
                                                Label { visible: Boolean(modelData.plan); text: "模型公开说明：" + modelData.plan; color: root.muted; wrapMode: Text.Wrap; width: parent.width }
                                                Repeater {
                                                    model: modelData.tools || []
                                                    delegate: Label {
                                                        required property var modelData
                                                        width: parent.width; wrapMode: Text.Wrap
                                                        text: "• " + (modelData.name || "工具") + " · " + (modelData.result || "已记录动作")
                                                        color: root.muted; font.pixelSize: 12
                                                    }
                                                }
                                                RowLayout {
                                                    Label { text: "新增草稿：" + ((modelData.staged || []).length) + " · 撤回：" + ((modelData.removed || []).length); color: root.muted; font.pixelSize: 11 }
                                                    Item { Layout.fillWidth: true }
                                                    Button { text: "查看本轮原始回复"; enabled: Boolean(root.selectedRunId); onClicked: bridge.loadModelResponse(root.selectedRunId, modelData.round) }
                                                }
                                            }
                                        }
                                    }
                                    Label { visible: !(root.currentTrace.steps || []).length; text: "选择运行记录后，查看其请求阶段和已记录的工具动作。"; color: root.muted; padding: 12 }
                                }
                            }
                            Rectangle {
                                visible: Boolean(root.currentResponse.raw || root.currentResponse.service_error)
                                Layout.fillWidth: true; Layout.preferredHeight: 140; radius: 8; color: "#f1f4f8"; border.color: root.line
                                ScrollView { anchors.fill: parent; anchors.margins: 10; TextArea { readOnly: true; text: root.currentResponse.raw || root.currentResponse.service_error || ""; wrapMode: TextEdit.Wrap; selectByMouse: true } }
                            }
                            Label { visible: root.errorText.length > 0; text: root.errorText; color: root.red; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            height: root.errorText.length > 0 ? 34 : 28
            color: root.white
            border.color: root.line
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 18; anchors.rightMargin: 18
                BusyIndicator { running: root.busy; visible: root.busy; implicitWidth: 20; implicitHeight: 20 }
                Label { text: root.busy ? "正在读取工程…" : (root.errorText || root.graphNotice || "本地图工程 · 通过 Operations 读写"); color: root.errorText ? root.red : root.muted; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true }
            }
        }
    }

    Popup {
        id: nodeTypesPopup; objectName: "nodeTypesPopup"
        x: Math.min(root.width - width - 20, 700); y: 140; width: 240; padding: 12
        Column {
            width: parent.width; spacing: 2
            Label { text: "节点类型（可多选）"; font.bold: true }
            Row {
                Button { text: "全部"; onClicked: { root.queryNodeTypes = null; root.runGraphQuery() } }
                Button { text: "清空"; onClicked: { root.queryNodeTypes = []; root.runGraphQuery() } }
            }
            Repeater {
                model: root.nodeTypeOptions
                delegate: CheckBox {
                    required property string modelData
                    objectName: "queryType_" + modelData
                    text: root.typeName(modelData)
                    checked: root.queryNodeTypes === null || root.queryNodeTypes.indexOf(modelData) >= 0
                    onClicked: root.toggleQueryFilter("types", modelData, checked)
                }
            }
        }
    }
    Popup {
        id: relationsPopup; objectName: "relationsPopup"
        x: Math.min(root.width - width - 20, 900); y: 140; width: 240; padding: 12
        Column {
            width: parent.width; spacing: 2
            Label { text: "关系类别（可多选）"; font.bold: true }
            Row {
                Button { text: "全部"; onClicked: { root.queryRelations = null; root.runGraphQuery() } }
                Button { text: "清空"; onClicked: { root.queryRelations = []; root.runGraphQuery() } }
            }
            Repeater {
                model: root.relationOptions
                delegate: CheckBox {
                    required property string modelData
                    objectName: "queryRelation_" + modelData
                    text: root.relationName(modelData)
                    checked: root.queryRelations === null || root.queryRelations.indexOf(modelData) >= 0
                    onClicked: root.toggleQueryFilter("relations", modelData, checked)
                }
            }
        }
    }
    Dialog {
        id: queryDialog; objectName: "queryDialog"
        title: "可复用查询条件"; modal: true; width: 620; height: 530
        anchors.centerIn: parent; standardButtons: Dialog.Close
        ColumnLayout {
            anchors.fill: parent
            Label { text: "复制后可使用相同条件重新查询。焦点与筛选不会修改正式图。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
            ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true
                TextArea { id: queryText; objectName: "queryJsonText"; text: root.queryJson; readOnly: true; selectByMouse: true; font.family: "Consolas"; wrapMode: TextEdit.Wrap }
            }
            Button { text: "复制查询"; onClicked: { queryText.selectAll(); queryText.copy() } }
        }
    }

    function typeColor(type) {
        return ({person:"#e9f0ff",group:"#f0eaff",item:"#fff4df",location:"#e6f5ed",event:"#e8f5f7",scene:"#f5eafa",storyline:"#fff0e8",other:"#eff2f6"})[type] || "#eff2f6"
    }
    function phaseName(value) {
        return ({research:"检索",draft:"起草",precheck:"预检",correction:"修正",submit:"提交",complete:"完成"})[value] || value || "未知阶段"
    }
    function requestName(value) {
        return ({waiting:"等待请求",in_flight:"模型正在请求",processing:"本地处理响应",paused_waiting:"已暂停，等待恢复",stopped:"请求已停止",not_started:"尚未开始"})[value] || value || "未知"
    }
}

