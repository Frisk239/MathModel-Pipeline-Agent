import type { Ref, WatchSource } from "vue";
import { nextTick, onMounted, ref, watch } from "vue";

const STICKY_THRESHOLD = 40;

/**
 * 粘底滚动：默认跟踪最新内容，用户上翻暂停，滚回底部恢复。
 *
 * @param scrollRef 滚动容器的 template ref
 * @param source 需要监听的响应式数据源（新数据到达时触发滚动）
 */
export function useStickyScroll(
	scrollRef: Ref<HTMLElement | null>,
	source: WatchSource,
) {
	const isPinnedToBottom = ref(true);

	function scrollToBottom() {
		const el = scrollRef.value;
		if (el) el.scrollTop = el.scrollHeight;
	}

	function onScroll() {
		const el = scrollRef.value;
		if (!el) return;
		isPinnedToBottom.value =
			el.scrollHeight - el.scrollTop - el.clientHeight <= STICKY_THRESHOLD;
	}

	watch(
		source,
		() => {
			if (isPinnedToBottom.value) nextTick(scrollToBottom);
		},
		{ deep: true },
	);

	onMounted(() => nextTick(scrollToBottom));

	return { onScroll };
}
