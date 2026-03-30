package ratelimit

import (
	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// init ensures uuid and redis/go-redis are direct dependencies
var (
	_ = uuid.New()
	_ = (*redis.Client)(nil)
)
