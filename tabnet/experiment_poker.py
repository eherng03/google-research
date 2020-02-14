# coding=utf-8
# Copyright 2020 The Google Research Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Experiment to train and evaluate the TabNet model on Poker."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import os
from absl import app
import data_helper_poker
import numpy as np
import tabnet_model
import tensorflow.compat.v1 as tf

# Run Tensorflow on GPU 0
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = "0"

# Training parameters
TRAIN_FILE = "data/train_poker.csv"
TEST_FILE = "data/test_poker.csv"
MAX_STEPS = 50000
DISPLAY_STEP = 500
TEST_STEP = 1000
save_step = 40000
INIT_LEARNING_RATE = 0.01
DECAY_EVERY = 500
DECAY_RATE = 0.95
BATCH_SIZE = 4096
SPARSITY_LOSS_WEIGHT = 0.000001
GRADIENT_THRESH = 2000.0
MAX_EPOCHS = 10000000
SEED = 1


def main(unused_argv):

  # Fix random seeds
  tf.set_random_seed(SEED)
  np.random.seed(SEED)

  # Define the TabNet model
  tabnet_poker = tabnet_model.TabNet(
      columns=data_helper_poker.get_columns(),
      num_features=data_helper_poker.NUM_FEATURES,
      feature_dim=32,
      output_dim=16,
      num_decision_steps=5,
      relaxation_factor=1.5,
      batch_momentum=0.9,
      virtual_batch_size=1024,
      num_classes=data_helper_poker.NUM_CLASSES)

  column_names = sorted(data_helper_poker.FEATURE_COLUMNS)
  print(
      "Ordered column names, corresponding to the indexing in Tensorboard visualization"
  )
  for fi in range(len(column_names)):
    print(str(fi) + " : " + column_names[fi])

  # Input sampling
  train_batch = data_helper_poker.input_fn(
      TRAIN_FILE, num_epochs=MAX_EPOCHS, shuffle=True, batch_size=BATCH_SIZE)
  test_batch = data_helper_poker.input_fn(
      TEST_FILE,
      num_epochs=MAX_EPOCHS,
      shuffle=False,
      batch_size=data_helper_poker.N_TEST_SAMPLES)

  train_iter = train_batch.make_initializable_iterator()
  test_iter = test_batch.make_initializable_iterator()

  feature_train_batch, label_train_batch = train_iter.get_next()
  feature_test_batch, label_test_batch = test_iter.get_next()

  # Define the model and losses

  encoded_train_batch, total_entropy = tabnet_poker.encoder(
      feature_train_batch, reuse=False, is_training=True)

  logits_orig_batch, _ = tabnet_poker.classify(encoded_train_batch, reuse=False)

  softmax_orig_key_op = tf.reduce_mean(
      tf.nn.sparse_softmax_cross_entropy_with_logits(
          logits=logits_orig_batch, labels=label_train_batch))

  train_loss_op = softmax_orig_key_op + SPARSITY_LOSS_WEIGHT * total_entropy
  tf.summary.scalar("Total loss", train_loss_op)

  # Optimization step
  global_step = tf.train.get_or_create_global_step()
  learning_rate = tf.train.exponential_decay(
      INIT_LEARNING_RATE,
      global_step=global_step,
      decay_steps=DECAY_EVERY,
      decay_rate=DECAY_RATE)
  optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate)
  update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
  with tf.control_dependencies(update_ops):
    gvs = optimizer.compute_gradients(train_loss_op)
    capped_gvs = [(tf.clip_by_value(grad, -GRADIENT_THRESH,
                                    GRADIENT_THRESH), var) for grad, var in gvs]
    train_op = optimizer.apply_gradients(capped_gvs, global_step=global_step)

  # Model evaluation

  # Test performance
  encoded_test_batch, _ = tabnet_poker.encoder(
      feature_test_batch, reuse=True, is_training=False)

  _, prediction_test = tabnet_poker.classify(encoded_test_batch, reuse=True)

  predicted_labels = tf.cast(tf.argmax(prediction_test, 1), dtype=tf.int32)
  test_eq_op = tf.equal(predicted_labels, label_test_batch)
  test_acc_op = tf.reduce_mean(tf.cast(test_eq_op, dtype=tf.float32))
  tf.summary.scalar("Test accuracy", test_acc_op)

  # Training setup
  model_name = "tabnet_poker_model"
  init = tf.initialize_all_variables()
  init_local = tf.local_variables_initializer()
  init_table = tf.tables_initializer(name="Initialize_all_tables")
  saver = tf.train.Saver()
  summaries = tf.summary.merge_all()

  with tf.Session() as sess:
    summary_writer = tf.summary.FileWriter("./tflog/" + model_name, sess.graph)

    sess.run(init)
    sess.run(init_local)
    sess.run(init_table)
    sess.run(train_iter.initializer)
    sess.run(test_iter.initializer)

    for step in range(1, MAX_STEPS + 1):
      if step % DISPLAY_STEP == 0:
        _, train_loss, merged_summary = sess.run(
            [train_op, train_loss_op, summaries])
        summary_writer.add_summary(merged_summary, step)
        print("Step " + str(step) + " , Training Loss = " +
              "{:.4f}".format(train_loss))
      else:
        _ = sess.run(train_op)

      if step % TEST_STEP == 0:
        feed_arr = [vars()["summaries"], vars()["test_acc_op"]]

        test_arr = sess.run(feed_arr)
        merged_summary = test_arr[0]
        test_acc = test_arr[1]

        print("Step " + str(step) + " , Test Accuracy = " +
              "{:.4f}".format(test_acc))
        summary_writer.add_summary(merged_summary, step)

      if step % save_step == 0:
        saver.save(sess, "./checkpoints/" + model_name + ".ckpt")


if __name__ == "__main__":
  app.run(main)
