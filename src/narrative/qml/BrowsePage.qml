import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

// 数据浏览：左侧分类，中间节点列表（按阅读顺序排，编号按类型各成一套），右侧详情 + 属性。
Item {
    id: page
    objectName: "browsePage"
    property var app
    readonly property var t: app.theme

    property var nodes: []
    property var steps: []
    property var events: []
    // 时间点的层级：大事件 → 场景 → 事件（带 depth，菜单按它缩进展开）。
    property var timeline: []
    // 当前阅读位置在步骤里的序号（0 = 最新）。
    readonly property int stepIndex: {
        for (var i = 0; i < steps.length; ++i)
            if (steps[i].id === cutEventId) return i + 1
        return 0
    }
    property var nameIndex: ({})
    property string selectedType: ""
    property string query: ""
    property string selectedId: ""
    property var detail: ({})
    property var counts: ({})
    property int revision: 0
    property int total: 0

    readonly property var typeLabels: ({
        person: "人物", group: "群体", item: "物品",
        location: "地点", event: "事件", scene: "场景", storyline: "故事线", other: "其他"
    })
    readonly property var typeKeys: ["person", "group", "item", "location", "event", "scene", "storyline", "other"]

    // 「最新」+ 当前粒度的每一步；编号即阅读顺序，所以顺序就是读者读到的顺序。
    readonly property var cutLabels: {
        var labels = ["最新"]
        for (var i = 0; i < steps.length; ++i)
            labels.push(steps[i].id + "  " + steps[i].name)
        return labels
    }

    // 属性名由模型给出（identity / height / state…），这里只补常见的中文说法。
    readonly property var propertyLabels: ({
        identity: "身份", state: "状态", status: "状况", holder: "持有者",
        height: "身高", age: "年龄", appearance: "外貌", attire: "衣着",
        clothing: "衣着", skin: "肤色", face: "面容", eyes: "眼睛",
        belief: "认知", knowledge: "知识", plan: "打算", location: "位置",
        color: "颜色", function: "功能", content: "内容", definition: "定义",
        nature: "性质", type: "类型", reputation: "名声", relation: "关系",
        quality: "品质", realm: "境界", cultivation: "修为", deed: "行径",
        injury: "伤势", effect: "效果", equipment: "装备", protection: "防护",
        spending: "花费", education: "学历", inscription: "题字",
        seclusion_duration: "闭关时长"
    })
    readonly property var scopeLabels: ({world: "世界", cognition: "认知", claim: "声称"})
    // 工程里一个节点都没有（区别于"被条件过滤掉了"）。
    property int projectTotal: 0
    readonly property bool isEmptyProject: projectTotal === 0

    ListModel { id: rows }
    ListModel { id: categories }

    // Plain JS arrays, not ListModel: a record carries nested arrays (related
    // nodes) and ListModel drops those on the way in.
    property var propertyRows: []      // 扁平列表，「全部」视图用
    property var categoryGroups: []    // 按大类分组
    property var inboundRows: []       // 别的节点指向本节点的记录
    property var claimRows: []         // 本节点说过的、关于别人的说法
    property var heardRows: []         // 别人说的、关于本节点的话
    property var moments: ({ levels: [], nodes: [] })   // 阅读位置的三层（后端给）
    property var childRows: []         // 下一级时间点：大事件→场景→事件
    // 节点资料：一张张资料卡，各有生效/失效时间。它和属性是同一类东西——
    // 「从这一步起成立，到那一步起不成立」——只是没有引文，所以不进属性记录。
    property var material: ({ node_id: "", cut: "", cards: [], hidden_count: 0,
                              moments: { levels: [], nodes: [] } })
    // 默认折叠成标题；展开状态按资料 id 记，刷新后不跳。
    property var expandedCards: ({})
    property string materialNodeId: ""
    property string cardEditId: ""
    property string pendingCardId: ""
    // 编辑器里的草稿：两个时间点、图片清单（已有的带 id，新加的带 path）。
    property string cardFromId: ""
    property string cardToId: ""
    property var draftImages: []
    // 分级选时间点：目标字段、每列选中的 id、当前选中的那个。
    property string momentTarget: ""
    property var pickedPath: []
    property string pickedId: ""
    property var vocabulary: []        // 当前节点类型的分类表
    property bool groupByCategory: true
    // 属性区回答哪个问题：latest = 这一刻的状态；all = 到这里为止说过的全部，
    // 失效的也列出来并逐条标注。两者在能失效之后就不是一回事了。
    property var expandedProps: ({})
    property var pickerTargets: []
    property var mergeNames: []
    // 事件等级：场景 / 事件。默认场景（粒度更粗，一次读一段）。
    property string granularity: "scene"
    // 阅读位置：cutId 是场景或事件的编号，空串表示看到最新。
    property string cutId: ""
    property string cutEventId: ""
    property string cutName: ""
    property int cutLater: 0
    // 默认只列当前这一步的相关节点：读者看某一步时，要的是这一步里的人、物、事，
    // 不是全书 164 个节点。没有选步骤（停在「最新」）时不筛。
    property bool relatedOnly: true
    // 同一个阅读位置在两种粒度下的编号：切粒度时停在这里，不跳回「最新」。
    property var cutAlt: ({})
    // 节点列表是否跟着阅读位置收窄。默认全部显示。
    property bool timeFilter: false
    // 属性区上一次用的切点：搜索时切点没变就不重载，展开状态得以保留。
    property string appliedCut: ""
    property string pendingFocus: ""

    // 查询、过滤、计数、切点解析都在后端：界面只提交条件并渲染结果。
    function load() {
        bridge.loadBrowse({
            granularity: page.granularity,
            cut_id: page.cutId,
            node_type: page.selectedType,
            query: page.query,
            related_only: page.relatedOnly,
            time_filter: page.timeFilter
        })
    }

    function nameOf(id) {
        var name = page.nameIndex[id]
        return name === undefined ? id : name
    }

    function applyBrowse(payload) {
        revision = payload.revision === undefined ? revision : payload.revision
        steps = payload.steps || []
        events = payload.events || []
        timeline = payload.timeline || []
        moments = payload.moments || ({ levels: [], nodes: [] })
        counts = payload.counts || ({})
        projectTotal = payload.project_total === undefined ? 0 : payload.project_total
        total = payload.total || 0
        nodes = payload.nodes || []
        var cut = payload.cut || null
        cutEventId = cut ? cut.event_id : ""
        cutName = cut ? cut.step_name : ""
        cutAlt = payload.cut_alt || ({})
        if (!cut) cutLater = 0

        var index = page.nameIndex
        for (var i = 0; i < nodes.length; ++i)
            if (nodes[i].id) index[nodes[i].id] = nodes[i].name || nodes[i].id
        page.nameIndex = index

        rows.clear()
        for (var j = 0; j < nodes.length; ++j) {
            var node = nodes[j]
            rows.append({
                id: node.id || "",
                labelId: node.label_id || node.id || "",
                name: node.name || "未命名",
                kindLabel: page.typeLabels[node.node_type] || node.node_type || "节点",
                note: node.identity_note || ""
            })
        }
        rebuildCategories()

        // 收窄或过滤后选中的节点可能不在列表里：不能留着一个"此刻还不存在"的详情。
        if (page.selectedId.length > 0 && !nodes.some(function (node) {
            return node.id === page.selectedId
        })) {
            page.clearSelection()
        }
        if (page.pendingFocus.length > 0) {
            for (var k = 0; k < rows.count; ++k) {
                if (rows.get(k).id === page.pendingFocus) {
                    nodeList.positionViewAtIndex(k, ListView.CenterInView)
                    break
                }
            }
            page.pendingFocus = ""
        }
        // 属性区和资料都跟同一个锚点事件走；切点没变就不重载。
        if (page.selectedId.length > 0 && page.appliedCut !== page.cutEventId) {
            page.appliedCut = page.cutEventId
            bridge.loadNodeProperties(page.selectedId)
            // 资料卡有自己的生效/失效时间：换阅读位置就得重新问一次。
            bridge.loadMaterial(page.selectedId)
        }
    }

    function rebuildCategories() {
        var all = 0
        for (var name in counts) all += counts[name]
        categories.clear()
        categories.append({key: "", label: "全部", count: all})
        for (var i = 0; i < page.typeKeys.length; ++i) {
            var key = page.typeKeys[i]
            categories.append({key: key, label: page.typeLabels[key], count: counts[key] || 0})
        }
    }

    function clearSelection() {
        selectedId = ""
        detail = ({})
        propertyRows = []
        categoryGroups = []
        inboundRows = []
        claimRows = []
        heardRows = []
        childRows = []
        expandedProps = ({})
        material = ({ node_id: "", cut: "", cards: [], hidden_count: 0,
            moments: { levels: [], nodes: [] } })
        expandedCards = ({})
        materialNodeId = ""
        appliedCut = ""
    }

    function pickType(kind) {
        selectedType = kind
        page.load()
    }

    // 换粒度时停在同一个阅读位置：场景的切点是它的最后一个事件，事件的切点是
    // 包含它的场景。位置由后端算好（cut_alt），界面只负责换成那个编号。
    function pickGranularity(value) {
        if (page.granularity === value) return
        var alt = page.cutAlt || ({})
        page.granularity = value
        page.cutId = alt[value] || ""
        page.appliedCut = ""
        page.load()
    }

    // 按步骤序号定位（0 = 最新）。菜单走的是 pickCutById，这个是给「上一步/下一步」用的。
    function pickCut(index) {
        page.pickCutById(
            index > 0 && index <= page.steps.length ? page.steps[index - 1].id : "")
    }

    // 点哪一级就停在哪一级：大事件停在它最后一场的最后一步，场景停在它最后一个事件。
    function pickCutById(id) {
        page.cutId = id || ""
        page.cutLater = 0
        page.load()
    }

    // 层级树里第 index 项的直接子项（按 depth 判断），返回下标数组。
    function timelineChildren(index) {
        var out = []
        if (index < 0 || index >= page.timeline.length) return out
        var base = page.timeline[index].depth
        for (var i = index + 1; i < page.timeline.length; ++i) {
            var row = page.timeline[i]
            if (row.depth <= base) break
            if (row.depth === base + 1) out.push(i)
        }
        return out
    }

    function timelineTop() {
        var out = []
        for (var i = 0; i < page.timeline.length; ++i)
            if (page.timeline[i].depth === 0) out.push(i)
        return out
    }

    function toggleTimeFilter() {
        page.timeFilter = !page.timeFilter
        page.load()
    }

    function toggleRelated() {
        page.relatedOnly = !page.relatedOnly
        page.load()
    }

    function backToLatest() {
        page.pickCutById("")
    }

    // 上一个 / 下一个时间点。下拉里 0 是「最新」，1..N 是各步骤，
    // 所以时间顺序是 1 → N → 最新(0)，两头各自接上。
    function stepCut(delta) {
        var count = page.steps.length
        if (count === 0) return
        // 当前位置可能是「步骤本身」，也可能是菜单里点的某个事件：两种都认。
        var index = 0
        for (var i = 0; i < count; ++i) {
            var step = page.steps[i]
            if (step.id === page.cutId || step.id === page.cutEventId) {
                index = i + 1
                break
            }
        }
        if (delta < 0) {
            if (index === 0) index = count
            else if (index > 1) index -= 1
        } else {
            if (index === 0) return
            index = index < count ? index + 1 : 0
        }
        page.pickCutById(index > 0 ? page.steps[index - 1].id : "")
    }

    function select(id) {
        selectedId = id
        detail = ({})
        propertyRows = []
        categoryGroups = []
        inboundRows = []
        claimRows = []
        heardRows = []
        childRows = []
        expandedProps = ({})
        appliedCut = page.cutEventId
        material = ({ node_id: "", cut: "", cards: [], hidden_count: 0,
            moments: { levels: [], nodes: [] } })
        expandedCards = ({})
        materialNodeId = ""
        bridge.nodeDetail(id)
        bridge.loadNodeProperties(id)
        bridge.loadMaterial(id)
    }

    // -- 分级时间点：一列一层，粗的在前 ---------------------------------
    // 层级表来自后端（`browse_view.MOMENT_LEVELS`），这里不写死「场景→事件」，
    // 多一层（卷、章，或者事件里的动作）只是多一列。

    // 层级表跟着**传进来的那棵树**走：阅读位置菜单用它自己那份（browse 载荷），
    // 资料卡用它那份（material 载荷）。写死读 material 会让菜单一列都没有。
    function momentLevels(tree) {
        return (tree || page.moments || ({})).levels || []
    }

    function momentNode(id) {
        var nodes = (page.material.moments || {}).nodes || []
        for (var i = 0; i < nodes.length; ++i)
            if (nodes[i].id === id) return nodes[i]
        return null
    }

    // 「E5 接电话」——编号比名字短，先给编号。
    function momentText(id) {
        if (!id) return ""
        var node = page.momentNode(id)
        return node ? (node.label_id + " " + node.name) : id
    }

    // 某一列的候选：只取**这一层**、并且挂在上一个选择下面的。
    //
    // 按 `level`（节点类型）筛，不按 `rank` 数字筛：数字是后端算出来的位置，
    // 一旦后端与界面版本对不上（或者老数据里场景没有大事件），数字就会把场景
    // 排进第一列。层级名是稳定的，宁可空着也不许串列。
    function momentRows(tree, rank, parentId) {
        var source = tree || ({})
        var levels = source.levels || []
        var key = rank < levels.length ? levels[rank].key : ""
        var nodes = source.nodes || []
        var out = []
        for (var i = 0; i < nodes.length; ++i) {
            var node = nodes[i]
            if (node.level !== key) continue
            if ((node.parent_id || "") === (parentId || "")) out.push(node)
        }
        return out
    }

    function pickMoment(rank, id) {
        var path = page.pickedPath.slice(0, rank)
        path.push(id)
        page.pickedPath = path
        page.pickedId = id
    }

    // 桶不是一条数据，点它只展开下一列，不改变已选中的阅读位置。
    function expandMoment(rank, id) {
        var path = page.pickedPath.slice(0, rank)
        path.push(id)
        page.pickedPath = path
    }

    // 一张卡的有效区间，折叠时就写在标题旁边。
    function cardWindow(card) {
        var from = card.valid_from ? page.momentText(card.valid_from) : "从头"
        var to = card.valid_to ? page.momentText(card.valid_to) : "一直"
        return from + " → " + to
    }

    function toggleCard(id) {
        var next = ({})
        for (var key in page.expandedCards) next[key] = page.expandedCards[key]
        next[id] = next[id] !== true
        page.expandedCards = next
    }

    function applyMaterial(payload) {
        // 慢结果不能覆盖别人：切到另一个节点后，上一个节点的资料会迟到。
        if (!payload || payload.node_id !== page.selectedId) return
        var sameNode = page.materialNodeId === payload.node_id
        var previous = ({})
        var oldCards = page.material.cards || []
        for (var i = 0; i < oldCards.length; ++i) previous[oldCards[i].id] = true
        // 换节点：全部折叠（默认只看标题）。同一节点刷新：新加的那张展开，其余不动。
        var expanded = ({})
        if (sameNode) {
            var cards = payload.cards || []
            for (var j = 0; j < cards.length; ++j) {
                var id = cards[j].id
                if (previous[id] !== true || page.expandedCards[id] === true) expanded[id] = true
            }
        }
        page.expandedCards = expanded
        page.materialNodeId = payload.node_id
        page.material = payload
        if (payload.changed_visible === false)
            app.toast("这张资料不在当前阅读位置上，换个位置才看得到", true)
    }

    // -- 资料卡编辑器 ---------------------------------------------------

    function openCardDialog(card) {
        page.cardEditId = card ? (card.id || "") : ""
        cardTitle.text = card ? (card.title || "") : ""
        cardDetail.text = card ? (card.detail || "") : ""
        page.cardFromId = card ? (card.valid_from || "") : ""
        page.cardToId = card ? (card.valid_to || "") : ""
        var drafts = []
        var images = card ? (card.images || []) : []
        for (var i = 0; i < images.length; ++i) {
            drafts.push({
                id: images[i].id,
                path: "",
                caption: images[i].caption || "",
                preview: page.imageUrl(images[i].path)
            })
        }
        page.draftImages = drafts
        cardDialog.open()
    }

    function imageUrl(path) {
        return path ? "file:///" + String(path).replace(/\\/g, "/") : ""
    }

    function draftCaption(index, text) {
        var list = page.draftImages.slice()
        if (index < 0 || index >= list.length) return
        var item = list[index]
        page.draftImages = list.map(function (entry, at) {
            return at === index
                ? {id: entry.id, path: entry.path, caption: text, preview: entry.preview}
                : entry
        })
    }

    function dropDraftImage(index) {
        var list = page.draftImages.slice()
        if (index < 0 || index >= list.length) return
        list.splice(index, 1)
        page.draftImages = list
    }

    function addDraftImages(urls) {
        var list = page.draftImages.slice()
        for (var i = 0; i < urls.length; ++i)
            list.push({id: "", path: String(urls[i]), caption: "", preview: String(urls[i])})
        page.draftImages = list
    }

    // 已有的带 id（只改说明），新加的带 path（提交时拷进工程）。
    function draftImagesJson() {
        var out = []
        for (var i = 0; i < page.draftImages.length; ++i) {
            var item = page.draftImages[i]
            out.push(item.id ? {id: item.id, caption: item.caption}
                             : {path: item.path, caption: item.caption})
        }
        return JSON.stringify(out)
    }

    function submitCard() {
        if (page.selectedId.length === 0) return
        if (page.cardEditId.length > 0)
            bridge.updateMaterialCard(page.selectedId, page.cardEditId,
                cardTitle.text, cardDetail.text, page.cardFromId, page.cardToId,
                page.draftImagesJson())
        else
            bridge.addMaterialCard(page.selectedId, cardTitle.text,
                cardDetail.text, page.cardFromId, page.cardToId,
                page.draftImagesJson())
        page.cardEditId = ""
        cardDialog.close()
    }

    // -- 分级选择 -------------------------------------------------------

    function openMomentPicker(target, currentId) {
        page.momentTarget = target
        // 从当前值往上找祖先，把每一列的选择摆好。
        var chain = []
        var id = currentId || ""
        while (id) {
            var node = page.momentNode(id)
            if (node === null) break
            chain.unshift(node.id)
            id = node.parent_id || ""
        }
        page.pickedPath = chain
        page.pickedId = currentId || ""
        momentPicker.open()
    }

    function pickedAt(rank) {
        return rank < page.pickedPath.length ? page.pickedPath[rank] : ""
    }

    function confirmMoment() {
        if (page.momentTarget === "from") page.cardFromId = page.pickedId
        else page.cardToId = page.pickedId
        momentPicker.close()
    }

    function previewImage(image) {
        imagePreview.source = page.imageUrl(image.path)
        imagePreview.caption = image.caption || ""
        imagePreview.open()
    }

    function askMaterialImage(cardId) {
        page.pendingCardId = cardId
        materialImagePicker.open()
    }

    function valueText(item) {
        if (item.value_node) return item.value_node.name
        if (item.value === null || item.value === undefined) return "（空）"
        return String(item.value)
    }

    function buildItem(item) {
        var records = item.records || []
        var ids = []
        for (var i = 0; i < records.length; ++i) ids.push(records[i].id)
        var scopes = item.scopes || [item.scope || "world"]
        var scopeText = ""
        for (var s = 0; s < scopes.length; ++s) {
            scopeText += (s > 0 ? "/" : "")
                + (page.scopeLabels[scopes[s]] || scopes[s] || "世界")
        }
        return {
            // 同一方面可能分在两个大类里，key 要带分类，否则展开状态会串。
            key: (item.aspect || item.property_id) + "|" + (item.category || ""),
            // 中文名由后端给（property_labels），未知属性名回退到原名。
            label: item.label || page.propertyLabels[item.property_id] || item.property_id || "",
            names: (item.property_ids || []).length > 1 ? item.property_ids.join(" / ") : "",
            nameIds: item.property_ids || [],
            category: item.category || "其他",
            categorySource: item.category_source || "",
            otherCategories: item.other_categories || [],
            recordIds: ids,
            valueText: (item.is_current === false && item.retracted !== true
                        ? "（历史）" : "") + page.valueText(item),
            valueNodeId: item.value_node ? item.value_node.id : "",
            scopeText: scopes.length > 1 ? scopeText : (page.scopeLabels[item.scope] || "世界"),
            mixedScope: scopes.length > 1,
            holderId: item.holder_id || "",
            holderName: item.holder_name
                || (item.holder_id ? page.nameOf(item.holder_id) : ""),
            // 有效与否是记录上的显式标记，不是算出来的区间：这里只说披露在哪儿。
            disclosedText: item.retracted ? "" : ("披露于 " + (item.disclosed_at || "—")),
            // 大事件这一行是从场景借来的：说清出处，读者才知道它是跟着阅读位置变的。
            derivedText: item.derived === true
                ? ("来自场景 " + ((item.from_scene || {}).name || "—") + "，随阅读位置更新")
                : "",
            records: records
        }
    }

    function applyProperties(payload) {
        var groups = payload.categories || []
        var built = []
        var all = []
        for (var g = 0; g < groups.length; ++g) {
            var items = []
            var source = groups[g].items || []
            for (var i = 0; i < source.length; ++i) {
                var row = page.buildItem(source[i])
                items.push(row)
                all.push(row)
            }
            built.push({
                key: groups[g].key, label: groups[g].key,
                count: groups[g].count || items.length, items: items
            })
        }
        vocabulary = payload.vocabulary || []
        inboundRows = payload.inbound || []
        claimRows = payload.claims || []
        heardRows = payload.heard || []
        childRows = payload.children || []
        cutLater = payload.later_count || 0
        categoryGroups = built
        propertyRows = all
        expandedProps = ({})
    }

    // 移动分类：只改整理位置，取值和披露顺序都不动。
    function askCategory(current, targets) {
        if (!targets || targets.length === 0) return
        pickerTargets = targets
        categoryPicker.current = current
        categoryPicker.open()
    }

    function applyCategory(category) {
        if (pickerTargets.length === 0) return
        bridge.categorizeProperties(JSON.stringify(pickerTargets), category)
    }

    // 属性归并：一行里出现多个名字时，选一个保留，其余按它读。
    // 归并和移动分类是两件事：一个改含义，一个只改位置。
    function askMerge(names) {
        if (!names || names.length < 2) return
        mergeNames = names
        mergeDialog.open()
    }

    function applyMerge(target) {
        for (var i = 0; i < mergeNames.length; ++i)
            if (mergeNames[i] !== target) bridge.mergeProperties(mergeNames[i], target)
        mergeDialog.close()
    }

    function openSource(sourceId, start) {
        app.openSource(sourceId, start)
    }

    // 改动落在阅读位置之后时要说清楚：记录确实写进去了，只是这一刻还读不到。
    function editToast(prefix, payload) {
        if (payload.outside_window !== true) return prefix
        return prefix + "，但它披露于「" + (payload.anchor_name || "该事件")
            + "」——在当前阅读位置之后，切到「最新」或那个事件才看得到"
    }

    function notifyEdit(prefix, payload) {
        app.toast(page.editToast(prefix, payload), payload.outside_window === true)
    }

    function openCategorySettings() {
        categoryEditor.text = page.vocabulary.join("\n")
        categoryDialog.open()
    }

    // 补录：人工写一条披露，仍然先成为候选。
    function eventIndex(id) {
        for (var i = 0; i < page.events.length; ++i)
            if (page.events[i].id === id) return i
        return -1
    }

    function openAddProperty() {
        addPropertyField.text = ""
        addValueField.text = ""
        addQuoteField.text = ""
        addCategoryField.currentIndex = Math.max(0, page.vocabulary.indexOf("其他"))
        // 停在某个阅读位置时，补的就是「此刻」：默认披露在这个事件，而不是第一个事件。
        var here = page.cutEventId.length > 0 ? page.eventIndex(page.cutEventId) : -1
        addEventField.currentIndex = here >= 0 ? here : 0
        addPropertyDialog.open()
    }

    function submitAddProperty() {
        var event = page.events[addEventField.currentIndex] || ({})
        var node = page.detail.node || ({})
        var sourceId = node.evidence ? node.evidence.source_id
            : (node.disclosure ? node.disclosure.source_id : "")
        bridge.addPropertyRecord({
            node_id: page.selectedId,
            property_id: addPropertyField.text,
            value: addValueField.text,
            event_id: event.id || "",
            category: addCategoryField.currentText,
            source: addSourceField.currentText,
            quote: addQuoteField.text,
            source_id: sourceId || ""
        })
        // 不在这里关：预检没过时要把填好的内容留在框里，等结果再决定。
    }

    function saveCategorySettings() {
        var lines = categoryEditor.text.split("\n")
        var names = []
        for (var i = 0; i < lines.length; ++i) {
            var name = lines[i].trim()
            if (name.length > 0 && name !== "其他" && names.indexOf(name) < 0) names.push(name)
        }
        if (names.length === 0) {
            app.toast("至少要保留一个大类", true)
            return
        }
        var config = {}
        config[detail.node ? detail.node.node_type : "other"] = names
        bridge.savePropertyCategories(JSON.stringify(config))
        categoryDialog.close()
    }

    function jumpTo(id) {
        if (!id) return
        // 目标节点可能被当前条件挡在列表外：只有真的挡住了才清条件。
        // 「相关节点」现在是默认开的，无条件清掉会让默认筛选项被一次跳转悄悄关掉。
        var listed = false
        for (var i = 0; i < page.nodes.length; ++i)
            if (page.nodes[i].id === id) { listed = true; break }
        if (!listed) {
            page.selectedType = ""
            page.query = ""
            page.relatedOnly = false
            searchField.text = ""
        }
        page.pendingFocus = id
        page.load()
        page.select(id)
    }

    function toggleProperty(key) {
        var next = ({})
        for (var name in expandedProps)
            next[name] = expandedProps[name]
        next[key] = !next[key]
        expandedProps = next
    }

    // Loads only once a project is open; the launcher has no project yet.
    Component.onCompleted: if (app.mode === "workspace") page.load()

    Connections {
        target: app
        function onModeChanged() {
            if (app.mode === "workspace") {
                page.selectedId = ""
                page.detail = ({})
                page.selectedType = ""
                page.query = ""
                searchField.text = ""
                propertyRows = []
                categoryGroups = []
                inboundRows = []
        claimRows = []
        heardRows = []
        childRows = []
                expandedProps = ({})
                page.cutId = ""
                page.cutEventId = ""
                page.cutName = ""
                page.cutLater = 0
                page.appliedCut = ""
                page.cutAlt = ({})
                page.relatedOnly = true
                page.timeFilter = false
                page.granularity = "scene"
                page.load()
            }
        }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "browse") {
                page.applyBrowse(payload)
            } else if (kind === "detail") {
                detail = payload
            } else if (kind === "property_correct") {
                if (payload.status === "accepted") {
                    app.toast(page.editToast("更正已生效", payload), payload.outside_window === true)
                    if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
                } else {
                    var problem = (payload.diagnostics || [])[0] || ({})
                    app.toast("更正未通过预检：" + (problem.message || "未知原因"), true)
                }
            } else if (kind === "property_retract") {
                var removed = (payload.retracted || []).length
                // 0 条不是「成功了但没说」，是「这些记录本来就已经失效过」。
                app.toast(removed > 0
                    ? "已撤回 " + removed + " 条记录"
                    : "这些记录已经失效过，不用再撤", removed === 0)
                if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
            } else if (kind === "material") {
                page.applyMaterial(payload)
            } else if (kind === "node_properties") {
                page.applyProperties(payload)
            } else if (kind === "property_categorize") {
                app.toast("已移到「" + (payload.category || "") + "」", false)
                if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
            } else if (kind === "property_merge") {
                app.toast("已合并到「" + (payload.target_id || "") + "」", false)
                if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
            } else if (kind === "property_add") {
                if (payload.status === "accepted") {
                    addPropertyDialog.close()
                    app.toast(page.editToast("补录已生效", payload), payload.outside_window === true)
                    if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
                } else {
                    var first = (payload.diagnostics || [])[0] || ({})
                    app.toast("补录未通过预检：" + (first.message || "未知原因"), true)
                }
            } else if (kind === "property_categories") {
                var nodeType = page.detail.node ? page.detail.node.node_type : "other"
                page.vocabulary = (payload.vocabulary || ({}) )[nodeType] || page.vocabulary
                if (payload.saved) {
                    app.toast("分类表已保存", false)
                    if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
                }
            } else if (kind === "node_edit") {
                app.toast("已提交修订，等待审核", false)
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            TextField {
                id: searchField
                objectName: "browseSearchField"
                Layout.preferredWidth: 280
                placeholderText: "搜索名称或别名"
                // 输入停下来再查，避免每个字都跑一次后端；回车就不等了。
                onTextChanged: { page.query = text; searchTimer.restart() }
                onAccepted: { searchTimer.stop(); page.load() }
            }
            Label {
                text: total + " 个节点 · 修订 " + revision
                color: t.muted
                font.pixelSize: 12
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "刷新"
                flat: true
                onClicked: page.load()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            // 列表是否跟着阅读位置收窄。
            Button {
                objectName: "browseTimeFilter"
                text: page.timeFilter ? "按时间显示" : "全部显示"
                flat: !page.timeFilter
                highlighted: page.timeFilter
                font.pixelSize: 11
                onClicked: page.toggleTimeFilter()
            }
            Label {
                text: "阅读位置"
                color: t.muted
                font.pixelSize: 12
            }
            Button {
                objectName: "browseCutPrev"
                text: "‹"
                flat: true
                font.pixelSize: 15
                enabled: page.steps.length > 0 && page.stepIndex > 1
                onClicked: page.stepCut(-1)
            }
            // 阅读位置：按层级展开——大事件 → 场景 → 事件。点哪一级就停在哪一级。
            Button {
                id: cutButton
                objectName: "browseCutButton"
                Layout.fillWidth: true
                Layout.maximumWidth: 320
                text: page.cutName.length > 0
                    ? page.cutName
                    : (page.timeline.length > 0 ? "最新（读到末尾）" : "没有时间点")
                enabled: page.timeline.length > 0
                onClicked: timelinePopup.open()
            }
            Button {
                objectName: "browseCutNext"
                text: "›"
                flat: true
                font.pixelSize: 15
                enabled: page.steps.length > 0 && page.stepIndex !== 0
                onClicked: page.stepCut(1)
            }
            Button {
                objectName: "browseCutReset"
                text: "最新"
                flat: true
                enabled: page.cutEventId.length > 0
                onClicked: page.backToLatest()
            }
            CheckBox {
                id: relatedBox
                objectName: "browseRelatedOnly"
                text: "相关节点"
                checked: page.relatedOnly
                font.pixelSize: 11
                onToggled: page.toggleRelated()
            }
            Item { Layout.fillWidth: true }
        }

        // 阅读位置的多级菜单：一列一层——大事件 → 场景 → 事件。
        // 点哪一层就把阅读位置停在那一段（大事件停在它最后一场的最后一步）。
        Popup {
            id: timelinePopup
            objectName: "browseTimelinePopup"
            modal: true
            padding: 0
            width: Math.min(860, page.width - 80)
            height: Math.min(420, page.height - 140)
            x: Math.round((page.width - width) / 2)
            y: 76
            // 选中的是三层的**编号**，不是列表下标：下标会被排序和桶搅乱，
            // 编号不会。桶（未归入大事件 / 未归入场景）只能展开，不能当阅读位置。
            property string firstId: ""
            property string secondId: ""
            property string thirdId: ""

            // 打开时落在第一段的第一个场景上：右边那列立刻有东西，不用先点一下。
            onOpened: {
                var tops = page.momentRows(page.moments, 0, "")
                firstId = tops.length > 0 ? tops[0].id : ""
                var kids = page.momentRows(page.moments, 1, firstId)
                secondId = kids.length > 0 ? kids[0].id : ""
                thirdId = ""
            }

            function choose(level, id, synthetic) {
                if (level === 0) {
                    firstId = id
                    var kids = page.momentRows(page.moments, 1, id)
                    secondId = kids.length > 0 ? kids[0].id : ""
                    thirdId = ""
                } else if (level === 1) {
                    secondId = id
                    thirdId = ""
                } else {
                    thirdId = id
                }
                if (!synthetic) page.pickCutById(id)
            }

            background: Rectangle {
                color: t.panel
                border.color: t.line
                radius: t.radius
            }

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                // 表头：一列一层，写清楚这一列是什么。
                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    spacing: 0
                    Repeater {
                        model: page.momentLevels(page.moments)
                        delegate: Label {
                            required property var modelData
                            required property int index
                            text: modelData.label
                            Layout.preferredWidth: index === 0 ? 300
                                : (index === 1 ? 280 : -1)
                            Layout.fillWidth: index === 2
                            leftPadding: 12
                            color: t.muted
                            font.pixelSize: 11
                            font.weight: Font.DemiBold
                        }
                    }
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: t.line }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 0

                    Repeater {
                        model: page.momentLevels(page.moments)
                        delegate: Item {
                            required property var modelData
                            required property int index
                            Layout.preferredWidth: index === 0 ? 300 : (index === 1 ? 280 : -1)
                            Layout.fillWidth: index === 2
                            Layout.fillHeight: true

                            ListView {
                                id: columnList
                                objectName: "browseTimelineLevel" + index
                                anchors.fill: parent
                                clip: true
                                model: index === 0
                                    ? page.momentRows(page.moments, 0, "")
                                    : page.momentRows(page.moments, index,
                                                      index === 1 ? timelinePopup.firstId
                                                                  : timelinePopup.secondId)
                                delegate: ItemDelegate {
                                    required property var modelData
                                    width: ListView.view.width
                                    highlighted: index === 0 ? timelinePopup.firstId === modelData.id
                                        : (index === 1 ? timelinePopup.secondId === modelData.id
                                                       : timelinePopup.thirdId === modelData.id)
                                    onClicked: timelinePopup.choose(index, modelData.id,
                                                                    modelData.synthetic === true)
                                    contentItem: Label {
                                        text: (modelData.label_id ? modelData.label_id + "　" : "")
                                            + modelData.name
                                            + (modelData.provisional === true ? "（暂称）" : "")
                                        elide: Text.ElideRight
                                        font.pixelSize: 12
                                        font.italic: modelData.synthetic === true
                                        color: modelData.synthetic === true ? t.muted : t.body
                                    }
                                }
                            }

                            // 空列要说清为什么空，不然看起来像坏了。
                            Label {
                                anchors.centerIn: parent
                                width: parent.width - 16
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.WordWrap
                                visible: columnList.count === 0
                                text: index === 0 ? "还没有大事件"
                                    : (index === 1 ? "先选一个大事件" : "先选一个场景")
                                color: t.muted
                                font.pixelSize: 10
                            }

                            Rectangle {
                                visible: index < 2
                                anchors.right: parent.right
                                width: 1
                                height: parent.height
                                color: t.line
                            }
                        }
                    }
                }
            }
        }

        // 搜索防抖：输入停下 200ms 才查。
        Timer {
            id: searchTimer
            interval: 200
            onTriggered: page.load()
        }

        // 三栏都能拖：分类 / 列表 / 详情。宽度是界面自己的事，不落后端。
        SplitView {
            id: columns
            objectName: "browseColumns"
            Layout.fillWidth: true
            Layout.fillHeight: true
            orientation: Qt.Horizontal

            handle: Rectangle {
                objectName: "browseColumnHandle"
                implicitWidth: 12
                color: "transparent"

                // 只负责把光标变成左右箭头；按键留给 SplitView 自己处理。
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.SplitHCursor
                    acceptedButtons: Qt.NoButton
                }

                Rectangle {
                    anchors.centerIn: parent
                    width: 2
                    height: parent.height
                    radius: 1
                    color: SplitHandle.pressed || SplitHandle.hovered ? t.accent : t.line
                }
            }

            // -- categories ---------------------------------------------
            Rectangle {
                SplitView.preferredWidth: 190
                SplitView.minimumWidth: 140
                SplitView.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 2

                    Label {
                        text: "分类"
                        color: t.muted
                        font.pixelSize: 11
                        Layout.bottomMargin: 4
                    }

                    Repeater {
                        model: categories

                        delegate: Item {
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 34

                            Rectangle {
                                anchors.fill: parent
                                radius: 5
                                color: page.selectedType === modelData.key ? t.accentSoft : "transparent"
                            }
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                spacing: 6
                                Label {
                                    text: modelData.label
                                    color: page.selectedType === modelData.key ? t.accent : t.ink
                                    font.pixelSize: 13
                                    Layout.fillWidth: true
                                }
                                Label {
                                    text: modelData.count
                                    color: modelData.count > 0 ? t.muted : t.line
                                    font.pixelSize: 11
                                }
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: page.pickType(modelData.key)
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

            // -- node list ----------------------------------------------
            Rectangle {
                SplitView.fillWidth: true
                SplitView.minimumWidth: 240
                SplitView.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ListView {
                    id: nodeList
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    model: rows
                    spacing: 2
                    delegate: Rectangle {
                        required property string id
                        required property string labelId
                        required property string name
                        required property string kindLabel

                        width: ListView.view.width
                        height: 42
                        radius: 4
                        color: page.selectedId === id ? t.accentSoft : "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: 10
                            Label {
                                text: labelId
                                color: t.muted
                                font.pixelSize: 11
                                Layout.preferredWidth: 48
                            }
                            Label {
                                text: kindLabel
                                color: t.accent
                                font.pixelSize: 11
                                Layout.preferredWidth: 48
                            }
                            Label {
                                text: name
                                color: page.selectedId === id ? t.accent : t.ink
                                font.pixelSize: 13
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: page.select(id)
                        }
                    }
                    Column {
                        anchors.centerIn: parent
                        spacing: 6
                        visible: rows.count === 0

                        Label {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: page.isEmptyProject
                                ? "这个工程还没有节点。模型提取的候选要先在「审核」里接受，才会进图。"
                                : "该分类下没有节点。"
                            color: t.muted
                        }
                        Label {
                            anchors.horizontalCenter: parent.horizontalCenter
                            visible: page.isEmptyProject
                            objectName: "goReviewLink"
                            text: "去审核 →"
                            color: t.accent
                            font.underline: true
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: app.currentPage = "review"
                            }
                        }
                    }
                }
            }

            // -- detail --------------------------------------------------
            Rectangle {
                SplitView.preferredWidth: 500
                SplitView.minimumWidth: 360
                SplitView.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 8

                    Label {
                        text: detail.node
                            ? (detail.display_name || detail.node.name)
                              + (detail.name_provisional ? "（暂称）" : "")
                            : "未选择节点"
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                        color: t.ink
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                    Label {
                        Layout.fillWidth: true
                        text: detail.node
                            ? "编号 " + (detail.node.label_id || detail.node.id) + " · "
                              + (page.typeLabels[detail.node.node_type] || detail.node.node_type)
                            : "在中间选择一个节点"
                        color: t.muted
                        font.pixelSize: 12
                    }

                    ScrollView {
                        id: detailScroll
                        objectName: "browseDetailScroll"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true

                        ColumnLayout {
                            width: detailScroll.availableWidth
                            spacing: 8

                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                visible: text.length > 0
                                text: detail.node && (detail.node.aliases || []).length > 0
                                    ? "别名：" + detail.node.aliases.join("、") : ""
                                color: t.muted
                                font.pixelSize: 12
                            }
                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                visible: text.length > 0
                                text: detail.node && detail.node.identity_note ? detail.node.identity_note : ""
                                color: t.body
                                font.pixelSize: 13
                            }
                            // 大事件是一段而不是一点：它串起的那一段阅读范围写在这里。
                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                visible: !!(detail.span)
                                text: detail.span
                                    ? "跨度：" + page.nameOf(detail.span.first) + " → "
                                      + page.nameOf(detail.span.last) + "（"
                                      + detail.span.scene_count + " 个场景 / "
                                      + detail.span.event_count + " 个事件）"
                                    : ""
                                color: t.accent
                                font.pixelSize: 12
                            }
                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                visible: text.length > 0
                                text: detail.node && detail.node.evidence
                                    ? "原文：" + detail.node.evidence.quote : ""
                                color: t.muted
                                font.pixelSize: 12
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: text.length > 0
                                text: detail.node && detail.node.disclosure
                                    ? "披露位置：" + detail.node.disclosure.start : ""
                                color: t.muted
                                font.pixelSize: 11
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 1
                                Layout.topMargin: 4
                                color: t.line
                            }

                            // 阅读位置生效时说明清楚：这是"读到这里"的状态，不是全部。
                            Label {
                                Layout.fillWidth: true
                                Layout.topMargin: 2
                                visible: page.cutEventId.length > 0
                                wrapMode: Text.Wrap
                                text: "读到「" + (page.cutName || page.cutEventId) + "」时的状态"
                                    + (page.stepIndex > 0
                                        ? "（第 " + page.stepIndex + " / "
                                            + page.steps.length + " 个时间点）" : "")
                                color: t.warn
                                font.pixelSize: 11
                            }

                            // 属性标题 + 整理动作一行放得下（详情栏窄时会被压，按钮各自有下限）。
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Label {
                                    text: "属性"
                                    color: t.ink
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                }
                                Label {
                                    text: propertyRows.length + " 项"
                                    color: t.muted
                                    font.pixelSize: 11
                                }
                                Item { Layout.fillWidth: true }
                                Button {
                                    objectName: "categoryModeButton"
                                    text: page.groupByCategory ? "分组" : "平铺"
                                    flat: true
                                    font.pixelSize: 10
                                    onClicked: page.groupByCategory = !page.groupByCategory
                                }
                                Button {
                                    objectName: "categorySettingsButton"
                                    text: "分类表"
                                    flat: true
                                    font.pixelSize: 10
                                    enabled: detail.node !== undefined && detail.node !== null
                                    onClicked: page.openCategorySettings()
                                }
                                Button {
                                    objectName: "addPropertyButton"
                                    text: "补录"
                                    flat: true
                                    font.pixelSize: 10
                                    enabled: page.selectedId.length > 0 && page.events.length > 0
                                    onClicked: page.openAddProperty()
                                }
                            }

                            // 下一级时间点：大事件下面是场景，场景下面是事件。
                            // 点一行就跳到那个时间点，可以一层层钻下去。
                            Column {
                                Layout.fillWidth: true
                                Layout.topMargin: 4
                                spacing: 2
                                visible: childRows.length > 0

                                RowLayout {
                                    width: parent.width
                                    spacing: 6
                                    Label {
                                        text: "包含"
                                        color: t.muted
                                        font.pixelSize: 11
                                        font.weight: Font.DemiBold
                                    }
                                    Label {
                                        text: childRows.length
                                        color: t.muted
                                        font.pixelSize: 10
                                    }
                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 1
                                        color: t.line
                                    }
                                }
                                Repeater {
                                    objectName: "browseChildRows"
                                    model: childRows

                                    delegate: RowLayout {
                                        required property var modelData
                                        width: parent.width
                                        spacing: 6

                                        Label {
                                            text: modelData.label_id
                                            color: t.muted
                                            font.pixelSize: 10
                                            Layout.preferredWidth: 30
                                        }
                                        Label {
                                            text: modelData.name
                                            color: t.accent
                                            font.pixelSize: 11
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                            font.underline: true
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: page.jumpTo(modelData.id)
                                            }
                                        }
                                        Label {
                                            text: modelData.type_label
                                            color: t.muted
                                            font.pixelSize: 10
                                        }
                                    }
                                }
                            }

                            // 按大类：一个大类一行标题，下面是它底下的属性。
                            // 分类只是整理位置，取值和披露顺序都不受影响。
                            Repeater {
                                objectName: "browseCategoryGroups"
                                model: page.groupByCategory ? page.categoryGroups : []

                                delegate: Column {
                                    required property var modelData

                                    Layout.fillWidth: true
                                    Layout.topMargin: 2
                                    spacing: 0
                                    visible: modelData.items.length > 0

                                    RowLayout {
                                        width: parent.width
                                        spacing: 6
                                        Label {
                                            text: modelData.label
                                            color: t.muted
                                            font.pixelSize: 11
                                            font.weight: Font.DemiBold
                                        }
                                        Label {
                                            text: modelData.count
                                            color: t.muted
                                            font.pixelSize: 10
                                        }
                                        Rectangle {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 1
                                            color: t.line
                                        }
                                    }
                                    Repeater {
                                        model: modelData.items

                                        delegate: PropertyItem {
                                            required property var modelData
                                            width: parent.width
                                            row: modelData
                                            host: page
                                        }
                                    }
                                }
                            }

                            // 全部：不分组，按属性名排。
                            Repeater {
                                model: page.groupByCategory ? [] : page.propertyRows

                                delegate: PropertyItem {
                                    required property var modelData
                                    width: parent.width
                                    row: modelData
                                    host: page
                                }
                            }

                            // 反向引用：别的节点上的记录指向本节点。同一份记录，
                            // 第二个入口，不复制、不改披露位置。
                            Column {
                                Layout.fillWidth: true
                                Layout.topMargin: 6
                                spacing: 2
                                visible: inboundRows.length > 0

                                RowLayout {
                                    width: parent.width
                                    spacing: 6
                                    Label {
                                        text: "反向引用"
                                        color: t.muted
                                        font.pixelSize: 11
                                        font.weight: Font.DemiBold
                                    }
                                    Label {
                                        text: inboundRows.length
                                        color: t.muted
                                        font.pixelSize: 10
                                    }
                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 1
                                        color: t.line
                                    }
                                }
                                Repeater {
                                    objectName: "browseInboundRows"
                                    model: inboundRows

                                    delegate: RowLayout {
                                        required property var modelData
                                        width: parent.width
                                        spacing: 6

                                        Label {
                                            text: modelData.category
                                            color: t.muted
                                            font.pixelSize: 10
                                            Layout.preferredWidth: 76
                                            elide: Text.ElideRight
                                        }
                                        Label {
                                            text: modelData.node_name
                                            color: t.accent
                                            font.pixelSize: 11
                                            font.underline: true
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: page.jumpTo(modelData.node_id)
                                            }
                                        }
                                        Label {
                                            text: "· " + (modelData.property_label
                                                || page.propertyLabels[modelData.property_id]
                                                || modelData.property_id) + " = "
                                                + (modelData.node_type ? "" : "")
                                                + page.nameOf(modelData.value)
                                            color: t.body
                                            font.pixelSize: 11
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                }
                            }
                            // 别人说的：不是它自己的状态，但它确实被这样说过。
                            // 放在这里，不混进上面的属性里。
                            Column {
                                Layout.fillWidth: true
                                Layout.topMargin: 6
                                spacing: 2
                                visible: heardRows.length > 0

                                RowLayout {
                                    width: parent.width
                                    spacing: 6
                                    Label {
                                        text: "别人说的"
                                        color: t.muted
                                        font.pixelSize: 11
                                        font.weight: Font.DemiBold
                                    }
                                    Label {
                                        text: heardRows.length
                                        color: t.muted
                                        font.pixelSize: 10
                                    }
                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 1
                                        color: t.line
                                    }
                                }
                                Repeater {
                                    objectName: "browseHeardRows"
                                    model: heardRows

                                    delegate: RowLayout {
                                        required property var modelData
                                        width: parent.width
                                        spacing: 6

                                        Label {
                                            text: modelData.scope_label
                                            color: t.muted
                                            font.pixelSize: 10
                                            Layout.preferredWidth: 28
                                        }
                                        Label {
                                            text: modelData.holder_name
                                            color: t.accent
                                            font.pixelSize: 11
                                            font.underline: true
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: page.jumpTo(modelData.holder_id)
                                            }
                                        }
                                        Label {
                                            text: "· " + modelData.property_label + " = "
                                                + page.nameOf(modelData.value)
                                            color: t.body
                                            font.pixelSize: 11
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                }
                            }
                            // 他说的 / 他以为的：这些记录记在**别人**的节点上，但说话的人
                            // 是他——所以显示在这里，被说的那个人的属性里不出现。
                            Column {
                                Layout.fillWidth: true
                                Layout.topMargin: 6
                                spacing: 2
                                visible: claimRows.length > 0

                                RowLayout {
                                    width: parent.width
                                    spacing: 6
                                    Label {
                                        text: "他说的 / 他以为的"
                                        color: t.muted
                                        font.pixelSize: 11
                                        font.weight: Font.DemiBold
                                    }
                                    Label {
                                        text: claimRows.length
                                        color: t.muted
                                        font.pixelSize: 10
                                    }
                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 1
                                        color: t.line
                                    }
                                }
                                Repeater {
                                    objectName: "browseClaimRows"
                                    model: claimRows

                                    delegate: RowLayout {
                                        required property var modelData
                                        width: parent.width
                                        spacing: 6

                                        Label {
                                            text: modelData.scope_label
                                            color: t.muted
                                            font.pixelSize: 10
                                            Layout.preferredWidth: 28
                                        }
                                        Label {
                                            text: "关于 " + modelData.node_name
                                            color: t.accent
                                            font.pixelSize: 11
                                            font.underline: true
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: page.jumpTo(modelData.node_id)
                                            }
                                        }
                                        Label {
                                            text: "· " + modelData.property_label + " = "
                                                + page.nameOf(modelData.value)
                                            color: t.body
                                            font.pixelSize: 11
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                }
                            }
                            // 资料：一张张带生效/失效时间的资料卡。它和属性是同一类东西——
                            // 「从这一步起成立，到那一步起不成立」——只是没有引文，所以不进
                            // 属性记录；浏览时同样只显示这一刻有效的，默认折叠成标题。
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: page.selectedId.length > 0
                                spacing: 6

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 1
                                    Layout.topMargin: 8
                                    color: t.line
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    Label {
                                        text: "资料"
                                        color: t.ink
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                    }
                                    Label {
                                        text: page.material.cards.length
                                        color: t.muted
                                        font.pixelSize: 11
                                    }
                                    Item { Layout.fillWidth: true }
                                    Button {
                                        objectName: "materialAddCard"
                                        text: "新增资料"
                                        flat: true
                                        font.pixelSize: 10
                                        enabled: page.selectedId.length > 0
                                        onClicked: page.openCardDialog(null)
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "和属性一样跟阅读位置走：只列这一刻有效的。点标题展开。"
                                    color: t.muted
                                    font.pixelSize: 10
                                    wrapMode: Text.Wrap
                                }

                                Repeater {
                                    objectName: "materialCards"
                                    model: page.material.cards

                                    delegate: ColumnLayout {
                                        required property var modelData
                                        readonly property var card: modelData
                                        readonly property bool open:
                                            page.expandedCards[card.id] === true

                                        Layout.fillWidth: true
                                        spacing: 0

                                        // 折叠时只剩这一行：标题 + 有效区间 + 两个动作。
                                        Rectangle {
                                            Layout.fillWidth: true
                                            implicitHeight: 28
                                            radius: 4
                                            color: open ? t.accentSoft : t.paper
                                            border.color: t.line

                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.leftMargin: 8
                                                anchors.rightMargin: 8
                                                spacing: 6

                                                Label {
                                                    text: (open ? "▾ " : "▸ ") + card.title
                                                    color: t.ink
                                                    font.pixelSize: 12
                                                    elide: Text.ElideRight
                                                    Layout.fillWidth: true
                                                    Layout.minimumWidth: 0
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        cursorShape: Qt.PointingHandCursor
                                                        onClicked: page.toggleCard(card.id)
                                                    }
                                                }
                                                Label {
                                                    text: page.cardWindow(card)
                                                    color: t.muted
                                                    font.pixelSize: 10
                                                }
                                                Label {
                                                    text: "编辑"
                                                    color: t.accent
                                                    font.pixelSize: 10
                                                    font.underline: true
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        cursorShape: Qt.PointingHandCursor
                                                        onClicked: page.openCardDialog(card)
                                                    }
                                                }
                                                Label {
                                                    text: "删除"
                                                    color: t.bad
                                                    font.pixelSize: 10
                                                    font.underline: true
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        cursorShape: Qt.PointingHandCursor
                                                        onClicked: bridge.removeMaterialCard(
                                                            page.selectedId, card.id)
                                                    }
                                                }
                                            }
                                        }

                                        ColumnLayout {
                                            visible: open
                                            Layout.fillWidth: true
                                            Layout.topMargin: 6
                                            spacing: 6

                                            Label {
                                                Layout.fillWidth: true
                                                visible: (card.detail || "").length > 0
                                                text: card.detail || ""
                                                color: t.body
                                                font.pixelSize: 11
                                                wrapMode: Text.Wrap
                                            }
                                            Flow {
                                                Layout.fillWidth: true
                                                spacing: 8
                                                Repeater {
                                                    model: card.images

                                                    delegate: Column {
                                                        required property var modelData
                                                        spacing: 2
                                                        Image {
                                                            objectName: "materialThumb"
                                                            width: 120
                                                            height: 120
                                                            source: modelData.path
                                                                ? "file:///" + String(modelData.path).replace(/\\/g, "/")
                                                                : ""
                                                            fillMode: Image.PreserveAspectFit
                                                            asynchronous: true
                                                            // 缩略图只有 120px：点开看大图。
                                                            MouseArea {
                                                                anchors.fill: parent
                                                                cursorShape: Qt.PointingHandCursor
                                                                onClicked: page.previewImage(modelData)
                                                            }
                                                        }
                                                        TextField {
                                                            width: 120
                                                            font.pixelSize: 10
                                                            placeholderText: "写一句说明"
                                                            text: modelData.caption || ""
                                                            onEditingFinished: if (text !== (modelData.caption || ""))
                                                                bridge.updateMaterialImageCaption(
                                                                    page.selectedId, card.id,
                                                                    modelData.id, text)
                                                        }
                                                        Label {
                                                            text: "移除"
                                                            color: t.bad
                                                            font.pixelSize: 9
                                                            font.underline: true
                                                            MouseArea {
                                                                anchors.fill: parent
                                                                cursorShape: Qt.PointingHandCursor
                                                                onClicked: bridge.removeMaterialImage(
                                                                    page.selectedId, card.id,
                                                                    modelData.id)
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                            RowLayout {
                                                Layout.fillWidth: true
                                                Button {
                                                    text: "+ 图片"
                                                    flat: true
                                                    font.pixelSize: 10
                                                    onClicked: page.askMaterialImage(card.id)
                                                }
                                                Item { Layout.fillWidth: true }
                                            }
                                        }
                                    }
                                }

                                Label {
                                    Layout.fillWidth: true
                                    visible: page.material.cards.length === 0
                                        && page.material.hidden_count === 0
                                    text: "还没有资料。可以建几张卡：一段设定、几张设定图，各写清从哪一步起成立。"
                                    color: t.muted
                                    font.pixelSize: 10
                                    wrapMode: Text.Wrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    visible: page.material.hidden_count > 0
                                    text: "另有 " + page.material.hidden_count
                                        + " 张在别的阅读位置上；换一下阅读位置才看得到。"
                                    color: t.muted
                                    font.pixelSize: 10
                                    wrapMode: Text.Wrap
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: t.line
                    }

                    // 节点字段、属性补录/更正/失效都在编辑器里，这里只留一个入口。
                    Button {
                        objectName: "browseOpenEditor"
                        Layout.fillWidth: true
                        text: "编辑节点"
                        enabled: page.selectedId.length > 0
                        onClicked: nodeEditor.openFor(page.selectedId)
                    }
                }
            }
        }
    }

    // 展开的卡上「+ 图片」：选一张，直接加到这张资料上。
    FileDialog {
        id: materialImagePicker
        title: "选择图片"
        nameFilters: ["图片 (*.png *.jpg *.jpeg *.webp *.gif)"]
        onAccepted: {
            if (page.pendingCardId.length > 0 && page.selectedId.length > 0)
                bridge.addMaterialImage(page.selectedId, page.pendingCardId,
                    materialImagePicker.selectedFile.toString(), "")
        }
    }

    // 编辑器里「+ 图片」：可以一次选多张，先摆在草稿里，保存时一起拷进来。
    FileDialog {
        id: draftImagePicker
        objectName: "materialDraftPicker"
        title: "选择图片"
        fileMode: FileDialog.OpenFiles
        nameFilters: ["图片 (*.png *.jpg *.jpeg *.webp *.gif)"]
        onAccepted: page.addDraftImages(draftImagePicker.selectedFiles)
    }

    // 点缩略图看大图：图原样铺开，说明在下面。
    Dialog {
        id: imagePreview
        objectName: "materialImagePreview"
        modal: true
        anchors.centerIn: parent
        width: Math.min(880, (page.width || 900) - 120)
        height: Math.min(720, (page.height || 800) - 80)
        padding: 0

        property string source: ""
        property string caption: ""

        contentItem: ColumnLayout {
            spacing: 6
            Image {
                Layout.fillWidth: true
                Layout.fillHeight: true
                source: imagePreview.source
                fillMode: Image.PreserveAspectFit
                asynchronous: true
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.margins: 10
                Label {
                    Layout.fillWidth: true
                    text: imagePreview.caption
                    color: t.muted
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }
                Button {
                    objectName: "materialImagePreviewClose"
                    text: "关闭"
                    flat: true
                    onClicked: imagePreview.close()
                }
            }
        }
    }

    // 新增 / 编辑资料卡：标题、详情、生效与失效（分级挑一步）、图片。
    Dialog {
        id: cardDialog
        objectName: "materialCardDialog"
        title: page.cardEditId.length > 0 ? "编辑资料" : "新增资料"
        modal: true
        anchors.centerIn: parent
        width: 480

        contentItem: ColumnLayout {
            spacing: 6
            TextField {
                id: cardTitle
                objectName: "materialCardTitle"
                Layout.fillWidth: true
                placeholderText: "标题，例如：青衫少年 / 认主之后"
                onAccepted: page.submitCard()
            }
            TextArea {
                id: cardDetail
                objectName: "materialCardDetail"
                Layout.fillWidth: true
                Layout.preferredHeight: 90
                wrapMode: TextArea.Wrap
                placeholderText: "详情：来历、特征、要注意的地方……"
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Label {
                    text: "生效"
                    color: t.muted
                    font.pixelSize: 11
                    Layout.preferredWidth: 32
                }
                Button {
                    objectName: "materialCardFrom"
                    Layout.fillWidth: true
                    flat: true
                    text: page.cardFromId ? page.momentText(page.cardFromId) : "（从一开始）"
                    onClicked: page.openMomentPicker("from", page.cardFromId)
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Label {
                    text: "失效"
                    color: t.muted
                    font.pixelSize: 11
                    Layout.preferredWidth: 32
                }
                Button {
                    objectName: "materialCardTo"
                    Layout.fillWidth: true
                    flat: true
                    text: page.cardToId ? page.momentText(page.cardToId) : "（一直有效）"
                    onClicked: page.openMomentPicker("to", page.cardToId)
                }
            }
            Label {
                Layout.fillWidth: true
                text: "时间点是读者读到的那一步：从这一步起成立，到那一步起不再算数。"
                color: t.muted
                font.pixelSize: 10
                wrapMode: Text.Wrap
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: t.line
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                Label {
                    text: "图片"
                    color: t.ink
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Label {
                    text: page.draftImages.length
                    color: t.muted
                    font.pixelSize: 10
                }
                Item { Layout.fillWidth: true }
                Button {
                    objectName: "materialCardAddImage"
                    text: "+ 图片"
                    flat: true
                    font.pixelSize: 10
                    onClicked: draftImagePicker.open()
                }
            }
            Flow {
                objectName: "materialCardImages"
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: page.draftImages
                    delegate: Column {
                        required property var modelData
                        required property int index
                        spacing: 2
                        Image {
                            width: 96
                            height: 96
                            source: modelData.preview
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true
                        }
                        TextField {
                            width: 96
                            font.pixelSize: 10
                            placeholderText: "说明"
                            text: modelData.caption
                            onTextChanged: page.draftCaption(index, text)
                        }
                        Label {
                            text: "移除"
                            color: t.bad
                            font.pixelSize: 9
                            font.underline: true
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: page.dropDraftImage(index)
                            }
                        }
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                visible: page.draftImages.length === 0
                text: "还没有图片。可以一次选多张，每张下面写一句说明。"
                color: t.muted
                font.pixelSize: 10
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button { text: "取消"; flat: true; onClicked: cardDialog.close() }
                Button {
                    objectName: "materialCardSubmit"
                    text: page.cardEditId.length > 0 ? "保存" : "新增"
                    highlighted: true
                    onClicked: page.submitCard()
                }
            }
        }
    }

    // 分级选时间点：一列一层。层级来自后端，多一层就是多一列。
    Popup {
        id: momentPicker
        objectName: "materialMomentPicker"
        modal: true
        anchors.centerIn: parent
        width: 720
        height: 420
        padding: 12

        contentItem: ColumnLayout {
            spacing: 6

            Label {
                Layout.fillWidth: true
                text: page.momentTarget === "from" ? "从哪一步起成立" : "到哪一步起不再算数"
                color: t.ink
                font.pixelSize: 12
                font.weight: Font.DemiBold
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: page.momentLevels(page.material.moments)
                    delegate: Label {
                        required property var modelData
                        Layout.fillWidth: true
                        text: modelData.label
                        color: t.muted
                        font.pixelSize: 10
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 8
                Repeater {
                    model: page.momentLevels(page.material.moments)
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        color: t.paper
                        radius: 4
                        border.color: t.line

                        ListView {
                            id: momentColumn
                            anchors.fill: parent
                            anchors.margins: 4
                            clip: true
                            spacing: 2
                            model: page.momentRows(page.material.moments, index,
                                                   index === 0 ? "" : page.pickedAt(index - 1))
                            delegate: Rectangle {
                                required property var modelData
                                width: ListView.view.width
                                // 大事件多一行：读者此刻知道它走到哪了。这句话来自
                                // 已经读过的场景，往后读会自己变长，不是写死的一条。
                                height: (modelData.summary || "").length > 0 ? 44 : 26
                                radius: 3
                                color: page.pickedId === modelData.id ? t.accentSoft : "transparent"

                                Column {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.leftMargin: 6
                                    anchors.rightMargin: 6
                                    spacing: 1

                                    Label {
                                        width: parent.width
                                        text: (modelData.label_id ? modelData.label_id + "  " : "")
                                            + modelData.name
                                            + (modelData.provisional === true ? "（暂称）" : "")
                                        color: page.pickedId === modelData.id
                                            ? t.accent
                                            : (modelData.synthetic === true ? t.muted : t.body)
                                        font.pixelSize: 11
                                        font.italic: modelData.synthetic === true
                                        elide: Text.ElideRight
                                    }
                                    Label {
                                        width: parent.width
                                        visible: (modelData.summary || "").length > 0
                                        text: modelData.summary || ""
                                        color: t.muted
                                        font.pixelSize: 10
                                        elide: Text.ElideRight
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: modelData.synthetic === true
                                        ? page.expandMoment(index, modelData.id)
                                        : page.pickMoment(index, modelData.id)
                                }
                            }

                            // 空列要说清为什么空：第一列空了是这个工程还没大事件，
                            // 后面两列空了是还没选上一层——层级是硬的，不猜。
                            Label {
                                anchors.centerIn: parent
                                width: parent.width - 16
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.WordWrap
                                visible: momentColumn.count === 0
                                text: index === 0 ? "还没有大事件"
                                    : (index === 1 ? "先选一个大事件" : "先选一个场景")
                                color: t.muted
                                font.pixelSize: 10
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Button {
                    objectName: "materialMomentClear"
                    text: page.momentTarget === "from" ? "从头开始" : "一直有效"
                    flat: true
                    font.pixelSize: 10
                    onClicked: {
                        page.pickedId = ""
                        page.pickedPath = []
                    }
                }
                Item { Layout.fillWidth: true }
                Button { text: "取消"; flat: true; onClicked: momentPicker.close() }
                Button {
                    objectName: "materialMomentConfirm"
                    text: "确定"
                    highlighted: true
                    onClicked: page.confirmMoment()
                }
            }
        }
    }

    NodeEditor {
        id: nodeEditor
        host: page
    }

    // 移动分类：只改整理位置。取值、披露顺序、原文引文都不动。
    Dialog {
        id: categoryPicker
        objectName: "categoryPicker"
        property string current: ""
        title: "移到哪个大类"
        modal: true
        anchors.centerIn: parent
        width: 320

        contentItem: ColumnLayout {
            spacing: 4
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "分类只决定显示位置，不改取值，也不改披露顺序。"
                color: t.muted
                font.pixelSize: 11
            }
            Repeater {
                model: page.vocabulary
                delegate: Button {
                    required property string modelData
                    Layout.fillWidth: true
                    flat: true
                    highlighted: modelData === categoryPicker.current
                    text: modelData + (modelData === categoryPicker.current ? "  ·当前" : "")
                    onClicked: {
                        page.applyCategory(modelData)
                        categoryPicker.close()
                    }
                }
            }
            Button {
                Layout.fillWidth: true
                flat: true
                text: "取消"
                onClicked: categoryPicker.close()
            }
        }
    }

    // 归并属性名：同一个方面用了两个名字时，选一个保留。
    Dialog {
        id: mergeDialog
        objectName: "mergeDialog"
        title: "合并成一个名字"
        modal: true
        anchors.centerIn: parent
        width: 340

        contentItem: ColumnLayout {
            spacing: 4
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "选一个保留的名字。其余名字的记录照旧保存，只是按这个名字读；"
                    + "撤回这次合并就恢复成两个名字。"
                color: t.muted
                font.pixelSize: 11
            }
            Repeater {
                model: page.mergeNames
                delegate: Button {
                    required property string modelData
                    Layout.fillWidth: true
                    flat: true
                    text: modelData
                    onClicked: page.applyMerge(modelData)
                }
            }
            Button {
                Layout.fillWidth: true
                flat: true
                text: "取消"
                onClicked: mergeDialog.close()
            }
        }
    }

    // 分类表：每行一个大类，顺序即显示顺序；「其他」固定在最后。
    Dialog {
        id: categoryDialog
        objectName: "categoryDialog"
        title: "分类表 · " + (detail.node
            ? (page.typeLabels[detail.node.node_type] || detail.node.node_type) : "")
        modal: true
        anchors.centerIn: parent
        width: 380

        contentItem: ColumnLayout {
            spacing: 6
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "每行一个大类，顺序即显示顺序。「其他」始终在最后，不用写。"
                color: t.muted
                font.pixelSize: 11
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 220
                TextArea {
                    id: categoryEditor
                    objectName: "categoryEditor"
                    wrapMode: TextArea.NoWrap
                    font.pixelSize: 13
                    selectByMouse: true
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button {
                    text: "取消"
                    flat: true
                    onClicked: categoryDialog.close()
                }
                Button {
                    objectName: "categorySaveButton"
                    text: "保存"
                    highlighted: true
                    onClicked: page.saveCategorySettings()
                }
            }
        }
    }

    // 补录：人工写一条披露。它仍然先成为候选，接受后才进正式资料。
    Dialog {
        id: addPropertyDialog
        objectName: "addPropertyDialog"
        title: "补一条属性"
        modal: true
        anchors.centerIn: parent
        width: 460

        contentItem: ColumnLayout {
            spacing: 6

            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "引文逐字复制，程序会核对位置；通过后立即生效（和它同一方面的更早的值，从这次披露起失效）。"
                color: t.muted
                font.pixelSize: 11
            }
            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 8
                rowSpacing: 6

                Label { text: "属性名"; color: t.muted; font.pixelSize: 11 }
                TextField {
                    id: addPropertyField
                    objectName: "addPropertyField"
                    Layout.fillWidth: true
                    placeholderText: "例如 state"
                    onAccepted: page.submitAddProperty()
                }
                Label { text: "值"; color: t.muted; font.pixelSize: 11 }
                TextField {
                    id: addValueField
                    objectName: "addValueField"
                    Layout.fillWidth: true
                    placeholderText: "照原文写"
                    onAccepted: page.submitAddProperty()
                }
                Label { text: "披露事件"; color: t.muted; font.pixelSize: 11 }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    ComboBox {
                        id: addEventField
                        objectName: "addEventField"
                        Layout.fillWidth: true
                        model: page.events.map(function (item) { return item.name })
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: page.cutEventId.length > 0
                        wrapMode: Text.Wrap
                        text: "默认是当前阅读位置「" + (page.cutName || "该事件") + "」"
                        color: t.muted
                        font.pixelSize: 9
                    }
                }
                Label { text: "大类"; color: t.muted; font.pixelSize: 11 }
                ComboBox {
                    id: addCategoryField
                    objectName: "addCategoryField"
                    Layout.fillWidth: true
                    model: page.vocabulary
                }
                Label { text: "来源"; color: t.muted; font.pixelSize: 11 }
                ComboBox {
                    id: addSourceField
                    objectName: "addSourceField"
                    Layout.fillWidth: true
                    model: ["narration", "speech", "thought", "observation", "document"]
                }
                Label { text: "原文引文"; color: t.muted; font.pixelSize: 11 }
                TextField {
                    id: addQuoteField
                    objectName: "addQuoteField"
                    Layout.fillWidth: true
                    placeholderText: "逐字复制；可留空，但补上才能回查"
                    onAccepted: page.submitAddProperty()
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button {
                    text: "取消"
                    flat: true
                    onClicked: addPropertyDialog.close()
                }
                Button {
                    objectName: "addPropertySubmit"
                    text: "提交补录"
                    highlighted: true
                    onClicked: page.submitAddProperty()
                }
            }
        }
    }
}
