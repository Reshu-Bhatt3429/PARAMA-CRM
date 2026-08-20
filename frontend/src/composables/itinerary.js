import { createResource } from 'frappe-ui'
import { ref } from 'vue'

/**
 * Whether this user may use the itinerary pages at all.
 *
 * The sidebar entry and the two routes are gated on it. The list itself is row
 * filtered on the server by `get_itinerary_permission_query_conditions`, so
 * this flag decides what is worth showing, never what is allowed.
 */
export const canUseItineraries = ref(false)

createResource({
  url: 'crm.api.itinerary.can_use_itineraries',
  cache: 'Can Use Itineraries',
  auto: true,
  onSuccess: (data) => {
    canUseItineraries.value = Boolean(data)
  },
})
