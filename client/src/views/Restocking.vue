<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else-if="candidates.length === 0" class="card empty-state">
      {{ t('restocking.empty') }}
    </div>
    <div v-else>
      <div class="card budget-card">
        <div class="budget-row">
          <label for="budget-slider" class="budget-label">{{ t('restocking.budget') }}</label>
          <span class="budget-value">{{ formatCurrency(budget, currentCurrency) }}</span>
        </div>
        <input
          id="budget-slider"
          v-model.number="budget"
          type="range"
          min="0"
          :max="maxBudget"
          :step="BUDGET_STEP"
          class="budget-slider"
        />
        <div class="budget-hint">{{ t('restocking.budgetHint') }}</div>
      </div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.budget') }}</div>
          <div class="stat-value">{{ formatCurrency(budget, currentCurrency) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.totalCost') }}</div>
          <div class="stat-value">{{ formatCurrency(totalCost, currentCurrency) }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.remaining') }}</div>
          <div class="stat-value">{{ formatCurrency(remaining, currentCurrency) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.itemCount') }} / {{ t('restocking.maxLeadTime') }}</div>
          <div class="stat-value">
            {{ recommended.length }} / {{ t('restocking.days', { days: maxLeadTime }) }}
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendedItems') }} ({{ recommended.length }})</h3>
          <button
            type="button"
            class="place-order-btn"
            :disabled="recommended.length === 0 || submitting"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="submitError" class="error submit-message">{{ submitError }}</div>
        <div v-if="placedOrder" class="success-message submit-message">
          {{ t('restocking.success', { orderNumber: placedOrder.order_number }) }}
          <router-link to="/orders">{{ t('restocking.viewOrders') }}</router-link>
        </div>

        <div v-if="recommended.length === 0" class="empty-state">
          {{ t('restocking.emptyRecommended') }}
        </div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.item') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.quantity') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.lineCost') }}</th>
                <th>{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in recommended" :key="item.item_sku">
                <td><strong>{{ item.item_sku }}</strong></td>
                <td>{{ translateProductName(item.item_name) }}</td>
                <td>
                  <span :class="['badge', item.trend]">{{ t(`trends.${item.trend}`) }}</span>
                </td>
                <td>{{ item.quantity }}</td>
                <td>{{ formatCurrencyWithDecimals(item.unit_cost, currentCurrency, 2) }}</td>
                <td><strong>{{ formatCurrency(item.line_cost, currentCurrency) }}</strong></td>
                <td>{{ t('restocking.days', { days: item.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="excluded.length > 0" class="card excluded-card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.excludedItems') }} ({{ excluded.length }})</h3>
        </div>
        <p class="excluded-hint">{{ t('restocking.excludedHint') }}</p>
        <div class="table-container">
          <table class="excluded-table">
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.item') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.quantity') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.lineCost') }}</th>
                <th>{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in excluded" :key="item.item_sku">
                <td>{{ item.item_sku }}</td>
                <td>{{ translateProductName(item.item_name) }}</td>
                <td>
                  <span :class="['badge', item.trend]">{{ t(`trends.${item.trend}`) }}</span>
                </td>
                <td>{{ item.quantity }}</td>
                <td>{{ formatCurrencyWithDecimals(item.unit_cost, currentCurrency, 2) }}</td>
                <td>{{ formatCurrency(item.line_cost, currentCurrency) }}</td>
                <td>{{ t('restocking.days', { days: item.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

const BUDGET_STEP = 500

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName } = useI18n()

    // Global filters (period/warehouse/category/status) are intentionally not used here:
    // forecasts are company-wide and have no warehouse, category or date dimension.
    const loading = ref(true)
    const error = ref(null)
    const candidates = ref([])
    const budget = ref(0)
    const submitting = ref(false)
    const submitError = ref(null)
    const placedOrder = ref(null)

    // Slider max = total cost of every candidate, rounded up to the next 1000,
    // so the far right end always means "buy everything".
    const maxBudget = computed(() => {
      const total = candidates.value.reduce((sum, c) => sum + c.line_cost, 0)
      return Math.ceil(total / 1000) * 1000
    })

    // Walk candidates in urgency order. An item that does not fit is skipped rather than
    // ending the loop, because a cheaper, lower-ranked item may still fit the remainder.
    const selection = computed(() => {
      const picked = []
      const skipped = []
      let left = budget.value
      for (const c of candidates.value) {
        // Small epsilon guards against float drift in summed line costs
        if (c.line_cost <= left + 1e-9) {
          picked.push(c)
          left -= c.line_cost
        } else {
          skipped.push(c)
        }
      }
      return { picked, skipped }
    })

    const recommended = computed(() => selection.value.picked)
    const excluded = computed(() => selection.value.skipped)

    const totalCost = computed(() =>
      recommended.value.reduce((sum, c) => sum + c.line_cost, 0)
    )
    const remaining = computed(() => Math.max(0, budget.value - totalCost.value))
    const maxLeadTime = computed(() =>
      recommended.value.reduce((max, c) => Math.max(max, c.lead_time_days), 0)
    )

    const loadCandidates = async () => {
      try {
        loading.value = true
        error.value = null
        candidates.value = await api.getRestockCandidates()
        // Default to half the max, snapped to the slider step so the thumb sits on a tick
        budget.value = Math.round(maxBudget.value / 2 / BUDGET_STEP) * BUDGET_STEP
      } catch (err) {
        error.value = t('restocking.loadError') + ': ' + err.message
        console.error('Restock load error:', err)
      } finally {
        loading.value = false
      }
    }

    const placeOrder = async () => {
      if (submitting.value || recommended.value.length === 0) return
      submitting.value = true
      submitError.value = null
      placedOrder.value = null
      try {
        // Budget is sent in USD; the server recomputes prices and re-validates against it
        placedOrder.value = await api.createRestockOrder({
          budget: budget.value,
          items: recommended.value.map(({ item_sku, quantity }) => ({ item_sku, quantity }))
        })
      } catch (err) {
        submitError.value =
          err.response?.data?.detail || t('restocking.orderError') + ': ' + err.message
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadCandidates)

    return {
      t,
      currentCurrency,
      translateProductName,
      formatCurrency,
      formatCurrencyWithDecimals,
      BUDGET_STEP,
      loading,
      error,
      candidates,
      budget,
      maxBudget,
      recommended,
      excluded,
      totalCost,
      remaining,
      maxLeadTime,
      submitting,
      submitError,
      placedOrder,
      placeOrder
    }
  }
}
</script>

<style scoped>
.budget-card {
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}

.budget-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.75rem;
}

.budget-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.budget-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
}

.budget-slider {
  width: 100%;
  accent-color: #0f172a;
}

.budget-hint {
  margin-top: 0.5rem;
  font-size: 0.813rem;
  color: #64748b;
}

.place-order-btn {
  background: #0f172a;
  color: white;
  border: none;
  border-radius: 6px;
  padding: 0.5rem 1.25rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
}

.place-order-btn:hover:not(:disabled) {
  background: #1e293b;
}

.place-order-btn:disabled {
  background: #cbd5e1;
  cursor: not-allowed;
}

.submit-message {
  margin: 0 1.5rem 1rem;
}

.success-message {
  padding: 0.75rem 1rem;
  background: #d1fae5;
  color: #065f46;
  border-radius: 6px;
}

.success-message a {
  margin-left: 0.75rem;
  color: #065f46;
  font-weight: 600;
}

.empty-state {
  padding: 1.5rem;
  color: #64748b;
}

.excluded-card {
  opacity: 0.75;
}

.excluded-hint {
  margin: 0 1.5rem 0.5rem;
  font-size: 0.875rem;
  color: #64748b;
}

.excluded-table {
  color: #64748b;
}
</style>
