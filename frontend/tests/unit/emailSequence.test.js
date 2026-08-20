import {
  isSequenceEmail,
  sequenceChipLabel,
  sequenceStage,
} from '@/utils/emailSequence'

const activity = (sequence_stage) => ({ data: { sequence_stage } })

describe('sequenceStage', () => {
  it('reads the stage number the server put on the activity', () => {
    expect(sequenceStage(activity(1))).toBe(1)
    expect(sequenceStage(activity(3))).toBe(3)
  })

  it('accepts the number as a string, because JSON is not typed', () => {
    expect(sequenceStage(activity('2'))).toBe(2)
  })

  it('reports no stage for an ordinary email', () => {
    // The overwhelmingly common case: nothing on the site uses sequences, so
    // the server sends null and the card must render exactly as it always did.
    expect(sequenceStage(activity(null))).toBe(0)
    expect(sequenceStage(activity(undefined))).toBe(0)
    expect(sequenceStage(activity(''))).toBe(0)
    expect(sequenceStage({})).toBe(0)
    expect(sequenceStage(null)).toBe(0)
  })

  it('refuses a value that is not a whole positive stage', () => {
    // A chip reading "Stage NaN" is worse than no chip at all.
    expect(sequenceStage(activity('nonsense'))).toBe(0)
    expect(sequenceStage(activity(0))).toBe(0)
    expect(sequenceStage(activity(-2))).toBe(0)
    expect(sequenceStage(activity(1.5))).toBe(0)
    expect(sequenceStage(activity(Infinity))).toBe(0)
  })
})

describe('sequenceChipLabel', () => {
  it('labels a sequence email with its stage', () => {
    expect(sequenceChipLabel(activity(2))).toBe('Sequence · Stage 2')
  })

  it('gives an ordinary email no chip', () => {
    expect(sequenceChipLabel(activity(null))).toBe(null)
    expect(sequenceChipLabel({})).toBe(null)
  })
})

describe('isSequenceEmail', () => {
  it('separates a sequence send from a message an agent typed', () => {
    expect(isSequenceEmail(activity(1))).toBe(true)
    expect(isSequenceEmail(activity(null))).toBe(false)
  })
})
